PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  source TEXT,
  task_type TEXT NOT NULL,
  objective TEXT NOT NULL,
  constraints TEXT,
  deadline TEXT,
  priority INTEGER DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'queued',
  assignee TEXT DEFAULT 'doer',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
  episode_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  skill_version TEXT,
  plan TEXT,
  actions TEXT,
  artifacts TEXT,
  outcome TEXT,
  confidence REAL,
  duration_sec INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS feedback (
  feedback_id TEXT PRIMARY KEY,
  episode_id TEXT NOT NULL,
  accepted INTEGER NOT NULL,
  edits_count INTEGER DEFAULT 0,
  rework INTEGER NOT NULL,
  escalation INTEGER NOT NULL,
  human_comment TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (episode_id) REFERENCES episodes(episode_id)
);

CREATE TABLE IF NOT EXISTS policy_changes (
  change_id TEXT PRIMARY KEY,
  from_version TEXT,
  to_version TEXT,
  hypothesis TEXT,
  diff TEXT,
  offline_score REAL,
  canary_score REAL,
  decision TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_episodes_task ON episodes(task_id);
CREATE INDEX IF NOT EXISTS idx_feedback_episode ON feedback(episode_id);
