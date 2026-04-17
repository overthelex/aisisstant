import os


class Config:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str

    # Collector intervals (seconds)
    input_bucket_seconds: int
    window_poll_seconds: float
    mic_poll_seconds: float
    score_window_seconds: int
    report_snapshot_seconds: float

    def __init__(self):
        self.db_host = os.environ.get("DB_HOST", "127.0.0.1")
        self.db_port = int(os.environ.get("DB_PORT", "5438"))
        self.db_name = os.environ.get("DB_NAME", "aisisstant")
        self.db_user = os.environ.get("DB_USER", "aisisstant")
        self.db_password = os.environ.get("DB_PASSWORD", "aisisstant_dev")

        self.input_bucket_seconds = int(os.environ.get("INPUT_BUCKET_SEC", "5"))
        self.window_poll_seconds = float(os.environ.get("WINDOW_POLL_SEC", "2"))
        self.mic_poll_seconds = float(os.environ.get("MIC_POLL_SEC", "10"))
        self.score_window_seconds = int(os.environ.get("SCORE_WINDOW_SEC", "30"))
        self.report_snapshot_seconds = float(
            os.environ.get("REPORT_SNAPSHOT_SEC", "1")
        )

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )
