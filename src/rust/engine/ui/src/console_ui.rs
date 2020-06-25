// Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
// Licensed under the Apache License, Version 2.0 (see LICENSE).

#![deny(warnings)]
// Enable all clippy lints except for many of the pedantic ones. It's a shame this needs to be copied and pasted across crates, but there doesn't appear to be a way to include inner attributes from a common source.
#![deny(
  clippy::all,
  clippy::default_trait_access,
  clippy::expl_impl_clone_on_copy,
  clippy::if_not_else,
  clippy::needless_continue,
  clippy::unseparated_literal_suffix,
  // TODO: Falsely triggers for async/await:
  //   see https://github.com/rust-lang/rust-clippy/issues/5360
  // clippy::used_underscore_binding
)]
// It is often more clear to show that nothing is being moved.
#![allow(clippy::match_ref_pats)]
// Subjective style.
#![allow(
  clippy::len_without_is_empty,
  clippy::redundant_field_names,
  clippy::too_many_arguments
)]
// Default isn't as big a deal as people seem to think it is.
#![allow(clippy::new_without_default, clippy::new_ret_no_self)]
// Arc<Mutex> can be more clear than needing to grok Orderings:
#![allow(clippy::mutex_atomic)]

use std::sync::Arc;
use std::future::Future;
use std::pin::Pin;
use std::time::Duration;

use futures::future::{self, FutureExt, TryFutureExt};
use indexmap::IndexMap;
use indicatif::{MultiProgress, ProgressBar, ProgressDrawTarget, ProgressStyle};
use uuid::Uuid;

use logging::logger::{StdioHandler, LOGGER};
use task_executor::Executor;
use workunit_store::WorkunitStore;

pub struct ConsoleUI {
  workunit_store: WorkunitStore,
  // While the UI is running, there will be an Instance present.
  instance: Option<Instance>,
}

impl ConsoleUI {
  pub fn new(workunit_store: WorkunitStore) -> ConsoleUI {
    ConsoleUI {
      workunit_store,
      instance: None,
    }
  }

  fn default_draw_target() -> ProgressDrawTarget {
    // NB: We render more frequently than we receive new data in order to minimize aliasing where a
    // render might barely miss a data refresh.
    ProgressDrawTarget::stderr_with_hz(Self::render_rate_hz() * 2)
  }

  ///
  /// The number of times per-second that `Self::render` should be called.
  ///
  pub fn render_rate_hz() -> u64 {
    10
  }

  pub fn render_interval() -> Duration {
    Duration::from_millis(1000 / Self::render_rate_hz())
  }

  pub async fn write_stdout(&mut self, msg: &str) -> Result<(), String> {
    if self.instance.is_some() {
      self.teardown().await?;
    }
    print!("{}", msg);
    Ok(())
  }

  pub fn write_stderr(&self, msg: &str) {
    if let Some(instance) = &self.instance {
      instance.bars[0].println(msg);
    } else {
      eprint!("{}", msg);
    }
  }

  pub async fn with_console_ui_disabled<F: FnOnce() -> T, T>(&mut self, f: F) -> T {
    if self.instance.is_some() {
      self.teardown().await.unwrap();
      f()
    } else {
      f()
    }
  }

  fn create_swimlane(msg: &str) -> ProgressBar {
    let style = ProgressStyle::default_bar().template("{spinner} {wide_msg}");
    let bar = ProgressBar::new(50).with_style(style);
    bar.set_message(msg);
    bar
  }

  fn get_label_from_heavy_hitters<'a>(
    tasks_to_display: impl Iterator<Item = (&'a String, &'a Option<Duration>)>,
  ) -> Vec<String> {
    tasks_to_display
      .map(|(label, maybe_duration)| {
        let duration_label = match maybe_duration {
          None => "(Waiting) ".to_string(),
          Some(duration) => {
            let duration_secs: f64 = (duration.as_millis() as f64) / 1000.0;
            format!("{:.2}s ", duration_secs)
          }
        };
        format!("{}{}", duration_label, label)
      })
      .collect()
  }

  ///
  /// Updates all of the swimlane ProgressBars with new data from the WorkunitStore. For this
  /// method to have any effect, the `initialize` method must have been called first.
  ///
  /// *Technically this method does not do the "render"ing: rather, the `MultiProgress` instance
  /// running on a background thread is drives rendering, while this method feeds it new data.
  ///
  pub fn render(&mut self) {
    let instance = if let Some(i) = &mut self.instance {
      i
    } else {
      return;
    };

    let multi_progress = &instance.multi_progress;
    let tasks_to_display = &mut instance.tasks_to_display;

    let max_displayed_swimlanes = 8;
    let heavy_hitters = self.workunit_store.heavy_hitters(max_displayed_swimlanes);

    // Insert every one in the set of tasks to display.
    // For tasks already here, the durations are overwritten.
    tasks_to_display.extend(heavy_hitters.clone().into_iter());

    // And remove the tasks that no longer should be there.
    for (task, _) in tasks_to_display.clone().into_iter() {
      if !heavy_hitters.contains_key(&task) {
        tasks_to_display.swap_remove(&task);
      }
    }

    let swimlane_labels: Vec<String> = Self::get_label_from_heavy_hitters(tasks_to_display.iter());
    let bars = &mut instance.bars;

    for (n, label) in swimlane_labels.iter().enumerate() {
      if n <= bars.len() {
        let swimlane = Self::create_swimlane(label);
        let swimlane = multi_progress.add(swimlane);
        bars.push(swimlane);
      } else {
        bars[n].set_message(label);
      }
    }

    let swimlanes = swimlane_labels.len();
    for n in swimlanes..max_displayed_swimlanes {
      multi_progress.remove(n);
      bars.remove(n);
    }
  }

  ///
  /// If the ConsoleUI is not already running, starts it.
  ///
  /// Errors if a ConsoleUI is already running (which would likely indicate concurrent usage of a
  /// Session attached to a console).
  ///
  pub fn initialize(
    &mut self,
    executor: Executor,
    stderr_handler: StdioHandler,
  ) -> Result<(), String> {
    if self.instance.is_some() {
      return Err("A ConsoleUI cannot render multiple UIs concurrently.".to_string());
    }

    // Setup bars, and then spawning rendering of the bars into a background task.
    //let (multi_progress, bars) = Self::setup_bars(num_cpus::get());
    let multi_progress_bars = MultiProgress::with_draw_target(Self::default_draw_target());

    let multi_progress = Arc::new(multi_progress_bars);
    let multi_progress_2 = Arc::clone(&multi_progress);
    let multi_progress_task = {
      executor
        .spawn_blocking(move || multi_progress_2.join())
        .boxed()
    };

    self.instance = Some(Instance {
      tasks_to_display: IndexMap::new(),
      multi_progress_task,
      logger_handle: LOGGER.register_stderr_handler(stderr_handler),
      multi_progress,
      bars: vec![],
    });
    Ok(())
  }

  ///
  /// If the ConsoleUI is running, completes it.
  ///
  pub fn teardown(&mut self) -> impl Future<Output = Result<(), String>> {
    if let Some(instance) = self.instance.take() {
      LOGGER.deregister_stderr_handler(instance.logger_handle);
      instance
        .multi_progress_task
        .map_err(|e| format!("Failed to render UI: {}", e))
        .boxed()
    } else {
      future::ok(()).boxed()
    }
  }
}

/// The state for one run of the ConsoleUI.
#[allow(dead_code)]
struct Instance {
  tasks_to_display: IndexMap<String, Option<Duration>>,
  multi_progress_task: Pin<Box<dyn Future<Output = std::io::Result<()>> + Send>>,
  bars: Vec<ProgressBar>,
  logger_handle: Uuid,
  multi_progress: Arc<MultiProgress>,
}
