use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;

// Embedded OneDrive cloud SVG — rendered as the panel icon
const CLOUD_SVG: &[u8] = include_bytes!("../assets/onedrive-cloud.svg");

use cosmic::app::{Core, Task};
use cosmic::iced::stream;
use cosmic::iced::window::Id;
use cosmic::iced::{Alignment, Length, Rectangle, Subscription, Vector};
use cosmic::surface::action::{app_popup, destroy_popup};
use cosmic::widget::menu;
use cosmic::Element;
use futures::channel::mpsc::Sender;
use futures::SinkExt;
use serde::Deserialize;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;
use tokio::time::sleep;

// ---------------------------------------------------------------------------
// Protocol types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize, PartialEq, Default)]
#[serde(rename_all = "lowercase")]
enum State {
    #[default]
    Idle,
    Uploading,
    Downloading,
    Syncing,
    Paused,
    Error,
}

impl State {
    fn label(&self) -> &'static str {
        match self {
            State::Idle => "Up to date",
            State::Uploading => "Uploading…",
            State::Downloading => "Downloading…",
            State::Syncing => "Syncing…",
            State::Paused => "Paused",
            State::Error => "Error",
        }
    }

}

#[derive(Debug, Clone, Deserialize, Default)]
struct DaemonStatus {
    #[serde(default)]
    state: State,
    #[serde(default)]
    pending: u32,
    #[serde(default)]
    last_sync: String,
    #[serde(default)]
    mounted: bool,
    #[serde(default)]
    error: String,
}

// ---------------------------------------------------------------------------
// App messages
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
enum Message {
    StatusUpdate(DaemonStatus),
    Disconnected,
    Surface(cosmic::surface::Action),
    OpenPopup(Rectangle, Vector),
    SyncNow,
    Pause,
    Resume,
    CommandSent(()),
}

// Right-click context menu actions
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ContextAction {
    SyncNow,
    Pause,
    Resume,
}

impl menu::Action for ContextAction {
    type Message = Message;
    fn message(&self) -> Message {
        match self {
            ContextAction::SyncNow => Message::SyncNow,
            ContextAction::Pause => Message::Pause,
            ContextAction::Resume => Message::Resume,
        }
    }
}

// ---------------------------------------------------------------------------
// App state
// ---------------------------------------------------------------------------

struct OneDriveApplet {
    core: Core,
    popup: Option<Id>,
    status: DaemonStatus,
    connected: bool,
    // Saved from last left-click for popup positioning
    last_bounds: Rectangle,
    last_offset: Vector,
}

fn socket_path() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join(".py-onedrive/control.sock")
}

// ---------------------------------------------------------------------------
// Subscription: persistent socket reader
// ---------------------------------------------------------------------------

fn daemon_subscription() -> Subscription<Message> {
    Subscription::run(|| {
        stream::channel(16, |mut tx: Sender<Message>| async move {
            loop {
                match UnixStream::connect(socket_path()).await {
                    Ok(stream) => {
                        let (read_half, _write_half) = stream.into_split();
                        let mut reader = BufReader::new(read_half);
                        let mut line = String::new();
                        loop {
                            line.clear();
                            match reader.read_line(&mut line).await {
                                Ok(0) => break,
                                Ok(_) => {
                                    let trimmed = line.trim();
                                    if trimmed.is_empty() {
                                        continue;
                                    }
                                    if let Ok(status) =
                                        serde_json::from_str::<DaemonStatus>(trimmed)
                                    {
                                        let _ = tx.send(Message::StatusUpdate(status)).await;
                                    }
                                }
                                Err(_) => break,
                            }
                        }
                        let _ = tx.send(Message::Disconnected).await;
                    }
                    Err(_) => {
                        let _ = tx.send(Message::Disconnected).await;
                    }
                }
                sleep(Duration::from_secs(3)).await;
            }
        })
    })
}

// ---------------------------------------------------------------------------
// Send a command to the daemon
// ---------------------------------------------------------------------------

async fn send_command(action: &'static str) -> bool {
    match UnixStream::connect(socket_path()).await {
        Ok(stream) => {
            let (read_half, mut write_half) = stream.into_split();
            let mut reader = BufReader::new(read_half);
            let mut line = String::new();
            let _ = reader.read_line(&mut line).await; // drain initial status push

            let cmd = format!("{{\"type\":\"cmd\",\"action\":\"{}\"}}\n", action);
            let _ = write_half.write_all(cmd.as_bytes()).await;

            line.clear();
            let _ = reader.read_line(&mut line).await;
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) {
                return v.get("ok").and_then(|x| x.as_bool()).unwrap_or(false);
            }
            false
        }
        Err(_) => false,
    }
}

// ---------------------------------------------------------------------------
// libcosmic Application impl
// ---------------------------------------------------------------------------

impl cosmic::Application for OneDriveApplet {
    type Executor = cosmic::SingleThreadExecutor;
    type Flags = ();
    type Message = Message;

    const APP_ID: &'static str = "com.github.ccampora.OneDriveApplet";

    fn core(&self) -> &Core {
        &self.core
    }

    fn core_mut(&mut self) -> &mut Core {
        &mut self.core
    }

    fn init(core: Core, _flags: ()) -> (Self, Task<Message>) {
        (
            OneDriveApplet {
                core,
                popup: None,
                status: DaemonStatus::default(),
                connected: false,
                last_bounds: Rectangle::default(),
                last_offset: Vector::default(),
            },
            Task::none(),
        )
    }

    fn on_close_requested(&self, _id: Id) -> Option<Message> {
        None
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::StatusUpdate(s) => {
                self.status = s;
                self.connected = true;
            }
            Message::Disconnected => {
                self.connected = false;
                self.status = DaemonStatus::default();
            }
            Message::Surface(a) => {
                return cosmic::task::message(cosmic::Action::Cosmic(
                    cosmic::app::Action::Surface(a),
                ));
            }
            Message::OpenPopup(bounds, offset) => {
                self.last_bounds = bounds;
                self.last_offset = offset;
                if let Some(id) = self.popup.take() {
                    return cosmic::task::message(cosmic::Action::Cosmic(
                        cosmic::app::Action::Surface(destroy_popup(id)),
                    ));
                }
                return cosmic::task::message(cosmic::Action::Cosmic(
                    cosmic::app::Action::Surface(app_popup::<OneDriveApplet>(
                        move |state: &mut OneDriveApplet| {
                            let id = Id::unique();
                            state.popup = Some(id);
                            let mut settings = state.core.applet.get_popup_settings(
                                state.core.main_window_id().unwrap(),
                                id,
                                None,
                                None,
                                None,
                            );
                            settings.positioner.anchor_rect = Rectangle {
                                x: (bounds.x - offset.x) as i32,
                                y: (bounds.y - offset.y) as i32,
                                width: bounds.width as i32,
                                height: bounds.height as i32,
                            };
                            settings
                        },
                        Some(Box::new(|state: &OneDriveApplet| {
                            state.status_popup_view().map(cosmic::Action::App)
                        })),
                    )),
                ));
            }
            Message::SyncNow => {
                return Task::perform(send_command("sync_now"), |_ok| {
                    cosmic::Action::App(Message::CommandSent(()))
                });
            }
            Message::Pause => {
                return Task::perform(send_command("pause"), |_ok| {
                    cosmic::Action::App(Message::CommandSent(()))
                });
            }
            Message::Resume => {
                return Task::perform(send_command("resume"), |_ok| {
                    cosmic::Action::App(Message::CommandSent(()))
                });
            }
            Message::CommandSent(()) => {}
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        // Left-click opens the status popup; right-click shows the context menu
        let btn = self
            .core
            .applet
            .icon_button_from_handle(self.panel_icon_handle())
            .on_press_with_rectangle(|offset, bounds| Message::OpenPopup(bounds, offset));

        cosmic::widget::context_menu(btn, self.context_items())
            .on_surface_action(Message::Surface)
            .window_id(self.core.main_window_id().unwrap_or(Id::RESERVED))
            .into()
    }

    fn view_window(&self, _id: Id) -> Element<'_, Message> {
        self.status_popup_view()
    }

    fn subscription(&self) -> Subscription<Message> {
        daemon_subscription()
    }

    fn style(&self) -> Option<cosmic::iced::theme::Style> {
        Some(cosmic::applet::style())
    }
}

impl OneDriveApplet {
    fn panel_icon_handle(&self) -> cosmic::widget::icon::Handle {
        // Use a named system icon for states that have clear standard representations;
        // fall back to the embedded OneDrive cloud for idle/connected.
        let named = match &self.status.state {
            _ if !self.connected => Some("network-offline-symbolic"),
            State::Uploading => Some("go-up-symbolic"),
            State::Downloading => Some("go-down-symbolic"),
            State::Syncing => Some("emblem-synchronizing-symbolic"),
            State::Paused => Some("media-playback-pause-symbolic"),
            State::Error => Some("dialog-error-symbolic"),
            State::Idle => None,
        };
        if let Some(name) = named {
            cosmic::widget::icon::from_name(name).into()
        } else {
            cosmic::widget::icon::from_svg_bytes(CLOUD_SVG)
        }
    }

    fn context_items(&self) -> Option<Vec<menu::Tree<Message>>> {
        // Always return Some — passing None causes a libcosmic context_menu panic in diff()
        let is_paused = self.status.state == State::Paused;
        let mut items: Vec<menu::Item<ContextAction, &'static str>> = vec![
            menu::Item::Button("Sync Now", None, ContextAction::SyncNow),
        ];
        if is_paused {
            items.push(menu::Item::Button("Resume", None, ContextAction::Resume));
        } else {
            items.push(menu::Item::Button("Pause", None, ContextAction::Pause));
        }
        Some(menu::items(&HashMap::new(), items))
    }

    fn status_popup_view(&self) -> Element<'_, Message> {
        let state_label = if self.connected {
            self.status.state.label()
        } else {
            "Daemon not running"
        };

        // Header: cloud icon + title + state
        let header = cosmic::widget::row::with_children(vec![
            self.panel_icon_handle().icon().size(32).into(),
            cosmic::widget::column::with_children(vec![
                cosmic::widget::text("Microsoft OneDrive").size(15).into(),
                cosmic::widget::text(state_label).size(12).into(),
            ])
            .spacing(2)
            .into(),
        ])
        .spacing(12)
        .align_y(Alignment::Center);

        // Status rows
        let mounted_label = if self.status.mounted { "Mounted ✓" } else { "Not mounted" };
        let pending_label = if self.status.pending == 0 {
            "No pending files".to_string()
        } else {
            format!("{} file(s) pending", self.status.pending)
        };
        let last_sync_label = if self.status.last_sync.is_empty() {
            "Never synced".to_string()
        } else {
            format!("Last sync: {}", self.status.last_sync)
        };

        let mut content = cosmic::widget::column::with_children(vec![
            header.into(),
            cosmic::widget::divider::horizontal::default().into(),
            cosmic::widget::text(mounted_label).size(13).into(),
            cosmic::widget::text(pending_label).size(13).into(),
            cosmic::widget::text(last_sync_label).size(12).into(),
        ])
        .spacing(6)
        .padding(16);

        if !self.status.error.is_empty() {
            content = content.push(
                cosmic::widget::text(format!("⚠  {}", self.status.error)).size(12),
            );
        }

        // Action buttons
        let is_paused = self.status.state == State::Paused;
        let pause_resume: Element<'_, Message> = if is_paused {
            cosmic::widget::button::text("Resume")
                .leading_icon(
                    cosmic::widget::icon::from_name("media-playback-start-symbolic"),
                )
                .on_press_maybe(self.connected.then_some(Message::Resume))
                .into()
        } else {
            cosmic::widget::button::text("Pause")
                .leading_icon(
                    cosmic::widget::icon::from_name("media-playback-pause-symbolic"),
                )
                .on_press_maybe(self.connected.then_some(Message::Pause))
                .into()
        };

        let buttons = cosmic::widget::row::with_children(vec![
            cosmic::widget::button::suggested("Sync Now")
                .leading_icon(cosmic::widget::icon::from_name(
                    "emblem-synchronizing-symbolic",
                ))
                .on_press_maybe(self.connected.then_some(Message::SyncNow))
                .into(),
            pause_resume,
        ])
        .spacing(8)
        .width(Length::Fill);

        content = content
            .push(cosmic::widget::divider::horizontal::default())
            .push(buttons);

        self.core.applet.popup_container(content).into()
    }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

fn main() -> cosmic::iced::Result {
    cosmic::applet::run::<OneDriveApplet>(())
}
