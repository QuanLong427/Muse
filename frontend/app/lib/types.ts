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

export type DownloadJobStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

export type DownloadJobItemStatus =
  | "queued"
  | "downloading"
  | "downloaded"
  | "failed"
  | "cancelled";

export interface DownloadRequestTrack {
  bvid: string;
  title?: string;
  author?: string;
  uploader?: string;
  videoTitle?: string;
}

export interface DownloadJobItem {
  id: string;
  position: number;
  bvid: string;
  url: string;
  title: string;
  artist: string;
  uploader: string;
  video_title: string;
  status: DownloadJobItemStatus;
  progress: number;
  result: Record<string, unknown>;
  error: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface DownloadJob {
  id: string;
  user_id: string;
  target_playlist_id?: string | null;
  status: DownloadJobStatus;
  total_items: number;
  completed_items: number;
  failed_items: number;
  progress: number;
  cancel_requested: boolean;
  result: Record<string, unknown>;
  last_error: string;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  items: DownloadJobItem[];
}

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
  playlistDraftIds?: string[];
}

export interface PlaylistDraft {
  id: string;
  user_id: string;
  session_id: string;
  revision: number;
  status: "draft" | "confirming" | "saved" | "cancelled";
  name: string;
  target_playlist_id: string;
  last_error: string;
  local_tracks?: Record<string, Track>;
  warnings: { code: string; message: string }[];
  items: { item_id: string; track: Track & { source_type?: "bilibili"; video_title?: string; uploader?: string }; reasons: { detail: string }[] }[];
  receipt: (SmartPlaylistSaveResult & { name: string }) | null;
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

export interface SmartPlaylistWarning {
  code: string;
  message: string;
}

export interface SmartPlaylistRecommendation {
  track: Track | SmartPlaylistRemoteCandidate;
  score: number;
  reasons: Array<{
    code: string;
    detail: string;
    evidence_score?: number;
  }>;
  duration_seconds: number;
}

export interface SmartPlaylistRemoteCandidate {
  id: string;
  title: string;
  author: string;
  bvid: string;
  duration: string;
  url: string;
  uploader: string;
  video_title: string;
  source_type: "bilibili";
}

export type SmartPlaylistSaveResult = NamedPlaylist & {
  download_job: DownloadJob | null;
  import_status: string;
};

export interface SmartPlaylistPreview {
  status: "ok" | "partial" | "empty";
  batch_id: string | null;
  scenario: string;
  suggested_name: string;
  tracks: Track[];
  remote_candidates: SmartPlaylistRemoteCandidate[];
  source_plan: { planned: { local: number; cloud: number }; actual: { local: number; cloud: number } };
  recommendations: SmartPlaylistRecommendation[];
  result_count: number;
  estimated_duration_seconds: number;
  duration_is_estimated: boolean;
  constraints: Record<string, unknown>;
  warnings: SmartPlaylistWarning[];
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
