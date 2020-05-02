// Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
// Licensed under the Apache License, Version 2.0 (see LICENSE).

use crate::scheduler::RootResult;
use std::time::Duration;
use std::sync::{mpsc};
use crate::core::{Failure, Value};
use indicatif::{MultiProgress, /*ProgressDrawTarget,*/ ProgressBar, ProgressStyle};
//use logging::logger::LOGGER;
use workunit_store::WorkUnitStore;
use indexmap::IndexMap;

pub struct ConsoleUI {
  workunit_store: WorkUnitStore,
  multi_progress_bars: MultiProgress,
}

impl ConsoleUI {
  pub fn new(workunit_store: WorkUnitStore) -> ConsoleUI {
    //let inner = InnerConsoleUI::new(workunit_store);
    ConsoleUI {
      workunit_store,
      multi_progress_bars: MultiProgress::new(),
    }
  }

  pub fn write_stdout(&self, _msg: &str) {
    //print!("XXX {}", msg);
  }

  pub fn write_stderr(&self, _msg: &str) {
    //eprint!("EEE {}", msg);
  }

  pub fn with_console_ui_disabled<F: FnOnce() -> T, T>(&self, f: F) -> T {
    f()
  }

  fn setup_bars(&self, num_swimlanes: usize) -> Vec<ProgressBar> {
    (0..num_swimlanes)
      .map(|_n| {
          let style = ProgressStyle::default_bar()
          .template("⚡ {spinner} {wide_msg}");
        self.multi_progress_bars.add(ProgressBar::new(50)
            .with_style(style.clone()))
        })
    .collect()
  }

  fn get_label_from_heavy_hitters<'a>(tasks_to_display: impl Iterator<Item = (&'a String, &'a Option<Duration>)>) -> Vec<String> {
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

  pub fn render_loop(&self, execution_status: mpsc::Receiver<Vec<Result<Value, Failure>>>,
      refresh_interval: Duration,
      ) -> Vec<RootResult> {
    //LOGGER.set_stderr_sink();
    let num_swimlanes = num_cpus::get();
    let bars: Vec<_> = self.setup_bars(num_swimlanes);
    let  _bar_to_write_to = bars[0].clone();
    let (display_loop_sender, display_loop_receiver) = mpsc::channel();

    let workunit_store = self.workunit_store.clone();
    let _ = std::thread::spawn(move || {
      let mut tasks_to_display: IndexMap<String, Option<Duration>> = IndexMap::new();
      let output = loop {
        let heavy_hitters = workunit_store.heavy_hitters(num_swimlanes);

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
        for (n, bar) in bars.iter().enumerate() {
          match swimlane_labels.get(n) {
            Some(label) => bar.set_message(label),
            None => bar.set_message(""),
          }
        }
        if let Ok(res) = execution_status.recv_timeout(refresh_interval) {
          break res;
        }
      };

      display_loop_sender.send(output).unwrap();
      for bar in bars.iter() {
        bar.finish_and_clear();
      }
    });

    self.multi_progress_bars.join_and_clear().unwrap();
    display_loop_receiver.recv().unwrap()
  }
}
