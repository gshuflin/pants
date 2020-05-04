// Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
// Licensed under the Apache License, Version 2.0 (see LICENSE).

#![allow(unused_imports)]
#![allow(unused_variables)]
use crate::scheduler::RootResult;
use std::time::Duration;
use std::sync::{mpsc, Arc};
use crate::core::{Failure, Value};
use indicatif::{MultiProgress, ProgressDrawTarget, ProgressBar, ProgressStyle};
//use logging::logger::LOGGER;
use workunit_store::WorkUnitStore;
use indexmap::IndexMap;
use parking_lot::Mutex;

pub enum ConsoleMessage {
  FinalResult(Vec<Result<Value, Failure>>),
  Stdout(String),
  Stderr(String),
}

pub struct ConsoleUI {
  workunit_store: WorkUnitStore,
  multi_progress_bars: MultiProgress,
  display_sender: Arc<Mutex<Option<mpsc::Sender<ConsoleMessage>>>>
}

impl ConsoleUI {
  pub fn new(workunit_store: WorkUnitStore) -> ConsoleUI {
    ConsoleUI {
      workunit_store,
      multi_progress_bars: MultiProgress::new(),
      display_sender: Arc::new(Mutex::new(None)),
    }
  }

  pub fn write_stdout(&self, msg: &str) {
    if let Some(ref sender) = *self.display_sender.lock() {
      sender.send(ConsoleMessage::Stdout(msg.to_string())).unwrap();
    }
  }

  pub fn write_stderr(&self, msg: &str) {
    if let Some(ref sender) = *self.display_sender.lock() {
      sender.send(ConsoleMessage::Stderr(msg.to_string())).unwrap();
    }
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

  pub fn render_loop(&self,
      display_sender: mpsc::Sender<ConsoleMessage>,
      execution_status: mpsc::Receiver<ConsoleMessage>,
      refresh_interval: Duration,
      ) -> Vec<RootResult> {

    {
      if let Some(ref mut mutex) = self.display_sender.try_lock() {
        **mutex = Some(display_sender);
      }
    }

    LOGGER.register_display_handle(display_sender.clone());
    let num_swimlanes = num_cpus::get();
    let bars: Vec<_> = self.setup_bars(num_swimlanes);
    let bar_to_write_to = bars[0].clone();
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
        match execution_status.recv_timeout(refresh_interval) {
          Ok(ConsoleMessage::FinalResult(res)) => break res,
          Ok(ConsoleMessage::Stdout(msg)) => {
            for bar in bars.iter() {
              bar.set_draw_target(ProgressDrawTarget::hidden());
            }
            print!("XXXXX {}", msg);
          },
          Ok(ConsoleMessage::Stderr(msg)) => {
            bar_to_write_to.println(msg)
          },
          _ => (),
        }
      };

      for bar in bars.iter() {
        bar.finish_and_clear();
      }
      display_loop_sender.send(output).unwrap();
    });

    self.multi_progress_bars.join().unwrap();
    display_loop_receiver.recv().unwrap()
  }
}
