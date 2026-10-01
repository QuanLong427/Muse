export interface Track {
  id: string;
  title: string;
  author: string;
  date: string;
  filename: string;
  subDir: string;
  size: number;
  url: string;
  bvid?: string;
}

export type TrackAction = "play" | "download" | "add_to_session" | "add_to_playlist";
export type DownloadStatus = "idle" | "queued" | "downloading" | "downloaded" | "failed";

export interface TrackCardData {
  track_id: string;
  source_type: "local" | "bilibili";
  availability: "local" | "remote" | "downloading" | "failed";
  title: string;
  author: string;
  duration?: string;
  bvid?: string | null;
  url: string;
  download_status: DownloadStatus;
  allowed_actions: TrackAction[];
  local_track?: Track | null;
}

export interface ChatMessage {
  id: string;
  role: "agent" | "operator" | "system" | "tool";
  content: string;
  timestamp: number;
  toolName?: string;
  trackCards?: TrackCardData[];
}

export interface PlayerState {
  current: Track | null;
  sessionId: string;
  revision: number;
  items: PlaybackSessionItem[];
  currentItemId: string | null;
  index: number;
  playing: boolean;
  progress: number;
  duration: number;
  volume: number;
  orderMode: PlaybackOrderMode;
  repeatMode: PlaybackRepeatMode;
}

export type PlaybackOrderMode = "sequential" | "shuffle" | "radio";
export type PlaybackRepeatMode = "off" | "all" | "one";

export type PlaybackOrigin =
  | "manual"
  | "playlist"
  | "smart_playlist"
  | "agent"
  | "radio"
  | "recommendation"
  | "legacy";

export interface PlaybackSessionItem {
  id: string;
  position: number;
  track: Track;
  origin_type: PlaybackOrigin;
  origin_id?: string | null;
  added_at: string;
}

export interface PlaybackSessionSnapshot {
  id: string;
  user_id: string;
  revision: number;
  current_item_id: string | null;
  status: "stopped" | "playing" | "paused";
  order_mode: PlaybackOrderMode;
  repeat_mode: PlaybackRepeatMode;
  progress_seconds: number;
  volume: number;
  history_item_ids: string[];
  history_cursor: number;
  shuffle_bag_item_ids: string[];
  items: PlaybackSessionItem[];
}

export interface NamedPlaylistItem {
  id: string;
  position: number;
  added_at: string;
  track: Track;
}

export interface NamedPlaylist {
  id: string;
  user_id: string;
  name: string;
  description: string;
  revision: number;
  created_at: string;
  updated_at: string;
  items: NamedPlaylistItem[];
}

export interface RecentTrack {
  track: Track;
  last_played_at: string;
  last_event_type: "play_started" | "play_resumed" | "play_completed";
  position_seconds: number;
  duration_seconds: number;
}

export interface AgentState {
  messages: ChatMessage[];
  loading: boolean;
  sessionId: string | null;
}
