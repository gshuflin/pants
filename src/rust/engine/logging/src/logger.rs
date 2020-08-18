// Copyright 2018 Pants project contributors (see CONTRIBUTORS.md).
// Licensed under the Apache License, Version 2.0 (see LICENSE).

use std::cell::RefCell;
use std::collections::HashMap;
use std::convert::{TryFrom, TryInto};
use std::fs::File;
use std::fs::OpenOptions;
use std::future::Future;
use std::io::{stderr, Stderr, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};

use colored::*;
use lazy_static::lazy_static;
use log::{debug, log, set_logger, set_max_level, LevelFilter, Log, Metadata, Record};
use parking_lot::Mutex;
use simplelog::{ConfigBuilder, LevelPadding, WriteLogger};
use tokio::task_local;
use uuid::Uuid;

use crate::PythonLogLevel;

const TIME_FORMAT_STR: &str = "%H:%M:%S";

pub type StdioHandler = Box<dyn Fn(&str) -> Result<(), ()> + Send>;

lazy_static! {
  pub static ref PANTS_LOGGER: PantsLogger = PantsLogger::new();
}

pub struct PantsLogger {
  pantsd_log: Mutex<MaybeWriteLogger<File>>,
  stderr_log: Mutex<MaybeWriteLogger<Stderr>>,
  use_color: AtomicBool,
  show_rust_3rdparty_logs: AtomicBool,
  show_log_domain: AtomicBool,
  stderr_handlers: Mutex<HashMap<Uuid, StdioHandler>>,
  log_level_filters: Mutex<RefCell<HashMap<String, log::LevelFilter>>>,
}

impl PantsLogger {
  pub fn new() -> PantsLogger {
    PantsLogger {
      pantsd_log: Mutex::new(MaybeWriteLogger::empty()),
      stderr_log: Mutex::new(MaybeWriteLogger::empty()),
      show_rust_3rdparty_logs: AtomicBool::new(true),
      use_color: AtomicBool::new(false),
      show_log_domain: AtomicBool::new(false),
      stderr_handlers: Mutex::new(HashMap::new()),
      log_level_filters: Mutex::new(RefCell::new(HashMap::new())),
    }
  }

  pub fn init(
    max_level: u64,
    show_rust_3rdparty_logs: bool,
    use_color: bool,
    show_log_domain: bool,
    log_domain_levels: HashMap<String, u64>,
  ) {
    let log_domain_levels = log_domain_levels
      .iter()
      .map(|(k, v)| {
        let python_level: PythonLogLevel = (*v).try_into().unwrap_or_else(|e| {
          panic!("Unrecognized log level from python: {}: {}", v, e);
        });
        let level: log::LevelFilter = python_level.into();
        (k.clone(), level)
      })
      .collect::<HashMap<_, _>>();

    let max_python_level: Result<PythonLogLevel, _> = max_level.try_into();
    match max_python_level {
      Ok(python_level) => {
        let level: log::LevelFilter = python_level.into();
        set_max_level(level);
        PANTS_LOGGER.use_color.store(use_color, Ordering::SeqCst);
        PANTS_LOGGER
          .show_log_domain
          .store(show_log_domain, Ordering::SeqCst);
        PANTS_LOGGER
          .show_rust_3rdparty_logs
          .store(show_rust_3rdparty_logs, Ordering::SeqCst);
        PANTS_LOGGER
          .log_level_filters
          .lock()
          .replace(log_domain_levels);
        if set_logger(&*PANTS_LOGGER).is_err() {
          debug!("Logging already initialized.");
        }
      }
      Err(err) => panic!("Unrecognised log level from Python: {}: {}", max_level, err),
    };
  }

  fn maybe_increase_global_verbosity(&self, new_level: log::LevelFilter) {
    if log::max_level() < new_level {
      set_max_level(new_level);
    }
  }

  pub fn set_stderr_logger(&self, python_level: u64) -> Result<(), String> {
    python_level
      .try_into()
      .map_err(|err| format!("{}", err))
      .map(|level: PythonLogLevel| {
        self.maybe_increase_global_verbosity(level.into());
        *self.stderr_log.lock() = MaybeWriteLogger::new(
          stderr(),
          level.into(),
          self.show_log_domain.load(Ordering::SeqCst),
          self.log_level_filters.lock().borrow().clone(),
        )
      })
  }

  ///
  /// Set up a file logger which logs at python_level to log_file_path.
  /// Returns the file descriptor of the log file.
  ///
  #[cfg(unix)]
  pub fn set_pantsd_logger(
    &self,
    log_file_path: PathBuf,
    python_level: u64,
  ) -> Result<std::os::unix::io::RawFd, String> {
    use std::os::unix::io::AsRawFd;
    python_level
      .try_into()
      .map_err(|err| format!("{}", err))
      .and_then(|level: PythonLogLevel| {
        {
          // Maybe close open file by dropping the existing logger
          *self.pantsd_log.lock() = MaybeWriteLogger::empty();
        }
        OpenOptions::new()
          .create(true)
          .append(true)
          .open(log_file_path)
          .map(|file| {
            let fd = file.as_raw_fd();
            self.maybe_increase_global_verbosity(level.into());
            *self.pantsd_log.lock() = MaybeWriteLogger::new(
              file,
              level.into(),
              self.show_log_domain.load(Ordering::SeqCst),
              self.log_level_filters.lock().borrow().clone(),
            );
            fd
          })
          .map_err(|err| format!("Error opening pantsd logfile: {}", err))
      })
  }

  /// log_from_python is only used in the Python FFI, which in turn is only called within the
  /// Python `NativeHandler` class. Every logging call from Python should get proxied through this
  /// function, which translates the log message into the Rust log paradigm provided by
  /// the `log` crate.
  pub fn log_from_python(message: &str, python_level: u64, target: &str) -> Result<(), String> {
    python_level
      .try_into()
      .map_err(|err| format!("{}", err))
      .map(|level: PythonLogLevel| {
        log!(target: target, level.into(), "{}", message);
      })
  }

  pub fn register_stderr_handler(&self, callback: StdioHandler) -> Uuid {
    let mut handlers = self.stderr_handlers.lock();
    let unique_id = Uuid::new_v4();
    handlers.insert(unique_id, callback);
    unique_id
  }

  pub fn deregister_stderr_handler(&self, unique_id: Uuid) {
    let mut handlers = self.stderr_handlers.lock();
    handlers.remove(&unique_id);
  }
}

impl Log for PantsLogger {
  fn enabled(&self, _metadata: &Metadata) -> bool {
    // Individual log levels are handled by each sub-logger,
    // And a global filter is applied to set_max_level.
    // No need to filter here.
    true
  }

  fn log(&self, record: &Record) {
    use chrono::Timelike;
    use log::Level;

    let mut should_log = self.show_rust_3rdparty_logs.load(Ordering::SeqCst);
    if !should_log {
      if let Some(ref module_path) = record.module_path() {
        for pants_package in super::pants_packages::PANTS_PACKAGE_NAMES {
          if &module_path.split("::").next().unwrap() == pants_package {
            should_log = true;
            break;
          }
        }
      } else {
        should_log = true;
      }
    }
    if !should_log {
      return;
    }

    let destination = get_destination();
    match destination {
      Destination::Stderr => {
        let cur_date = chrono::Local::now();
        let time_str = format!(
          "{}.{:02}",
          cur_date.format(TIME_FORMAT_STR),
          cur_date.time().nanosecond() / 10_000_000 // two decimal places of precision
        );

        let level = record.level();
        let show_log_domain = self.show_log_domain.load(Ordering::SeqCst);
        let use_color = self.use_color.load(Ordering::SeqCst);

        let level_marker = match level {
          _ if !use_color => format!("[{}]", level).normal().clear(),
          Level::Info => format!("[{}]", level).normal(),
          Level::Error => format!("[{}]", level).red(),
          Level::Warn => format!("[{}]", level).red(),
          Level::Debug => format!("[{}]", level).green(),
          Level::Trace => format!("[{}]", level).magenta(),
        };

        let log_string = if show_log_domain {
          format!(
            "{} {} {} {}",
            time_str,
            level_marker,
            record.target(),
            record.args()
          )
        } else {
          format!("{} {} {}", time_str, level_marker, record.args())
        };

        {
          // We first try to output to all registered handlers. If there are none, or any of them
          // fail, then we fallback to sending directly to stderr.
          let handlers_map = self.stderr_handlers.lock();
          let mut any_handler_failed = false;
          for callback in handlers_map.values() {
            let handler_res = callback(&log_string);
            if handler_res.is_err() {
              any_handler_failed = true;
            }
          }
          if handlers_map.len() == 0 || any_handler_failed {
            self.stderr_log.lock().log(record);
          }
        }
      }
      Destination::Pantsd => self.pantsd_log.lock().log(record),
    }
  }

  fn flush(&self) {
    self.stderr_log.lock().flush();
    self.pantsd_log.lock().flush();
  }
}

struct MaybeWriteLogger<W: Write + Send + 'static> {
  level: LevelFilter,
  log_domain_filters: HashMap<String, LevelFilter>,
  inner: Option<Box<WriteLogger<W>>>,
}

impl<W: Write + Send + 'static> MaybeWriteLogger<W> {
  pub fn empty() -> MaybeWriteLogger<W> {
    MaybeWriteLogger {
      level: LevelFilter::Off,
      log_domain_filters: HashMap::new(),
      inner: None,
    }
  }

  pub fn new(
    writable: W,
    level: LevelFilter,
    show_log_domain: bool,
    log_domain_filters: HashMap<String, LevelFilter>,
  ) -> MaybeWriteLogger<W> {
    // We initialize the inner WriteLogger with no filters so that we don't
    // have to create a new one every time we change the level of the outer
    // MaybeWriteLogger.

    let config = ConfigBuilder::new()
      .set_time_format_str(TIME_FORMAT_STR)
      .set_time_to_local(true)
      .set_target_level(if show_log_domain {
        LevelFilter::Error
      } else {
        LevelFilter::Off
      })
      .set_thread_level(LevelFilter::Off)
      .set_level_padding(LevelPadding::Off)
      .build();

    MaybeWriteLogger {
      level,
      log_domain_filters,
      inner: Some(WriteLogger::new(LevelFilter::max(), config, writable)),
    }
  }
}

impl<W: Write + Send + 'static> Log for MaybeWriteLogger<W> {
  fn enabled(&self, metadata: &Metadata) -> bool {
    let enabled_globally = metadata.level() <= self.level;
    let enabled_for_log_domain = self
      .log_domain_filters
      .get(metadata.target())
      .map(|lf| metadata.level() <= *lf)
      .unwrap_or(false);
    enabled_globally || enabled_for_log_domain
  }

  fn log(&self, record: &Record) {
    if !self.enabled(record.metadata()) {
      return;
    }
    if let Some(ref logger) = self.inner {
      logger.log(record);
    }
  }

  fn flush(&self) {
    if let Some(ref logger) = self.inner {
      logger.flush();
    }
  }
}

///
/// Thread- or task-local context for where the Logger should send log statements.
///
/// We do this in a per-thread way because we find that Pants threads generally are either
/// daemon-specific or user-facing. We make sure that every time we spawn a thread on the Python
/// side, we set the thread-local information, and every time we submit a Future to a tokio Runtime
/// on the rust side, we set the task-local information.
///
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
#[repr(C)]
pub enum Destination {
  Pantsd,
  Stderr,
}

impl TryFrom<&str> for Destination {
  type Error = String;
  fn try_from(dest: &str) -> Result<Self, Self::Error> {
    match dest {
      "pantsd" => Ok(Destination::Pantsd),
      "stderr" => Ok(Destination::Stderr),
      other => Err(format!("Unknown log destination: {:?}", other)),
    }
  }
}

thread_local! {
  static THREAD_DESTINATION: RefCell<Destination> = RefCell::new(Destination::Stderr)
}

task_local! {
  static TASK_DESTINATION: Destination;
}

///
/// Set the current log destination for a Thread, but _not_ for a Task. Tasks must always be spawned
/// by callers using the `scope_task_destination` helper (generally via task_executor::Executor.)
///
pub fn set_thread_destination(destination: Destination) {
  THREAD_DESTINATION.with(|thread_destination| {
    *thread_destination.borrow_mut() = destination;
  })
}

///
/// Propagate the current log destination to a Future representing a newly spawned Task. Usage of
/// this method should mostly be contained to task_executor::Executor.
///
pub async fn scope_task_destination<F>(destination: Destination, f: F) -> F::Output
where
  F: Future,
{
  TASK_DESTINATION.scope(destination, f).await
}

///
/// Get the current log destination, from either a Task or a Thread.
///
/// TODO: Having this return an Option and tracking down all cases where it has defaulted would be
/// good.
///
pub fn get_destination() -> Destination {
  if let Ok(destination) = TASK_DESTINATION.try_with(|destination| *destination) {
    destination
  } else {
    THREAD_DESTINATION.with(|destination| *destination.borrow())
  }
}
