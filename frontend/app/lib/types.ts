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

export type TrackAction = "play" | "download" | "add_to_queue" | "add_to_playlist";

export interface TrackCardData {
  track_id: string;
  source_type: "local" | "bilibili";
  availability: "local" | "remote" | "downloading" | "failed";
  title: string;
  author: string;
  duration?: string;
  bvid?: string | null;
  url: string;
  download_status: string;
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
  playlist: Track[];
  index: number;
  playing: boolean;
  progress: number;
  duration: number;
  volume: number;
}

export interface AgentState {
  messages: ChatMessage[];
  loading: boolean;
  sessionId: string | null;
}
