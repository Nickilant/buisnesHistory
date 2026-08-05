from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'casebook-updater'
    database_url: str = 'postgresql+psycopg2://app:app@postgres:5432/casebook'

    casebook_api_url: str = 'https://api3.casebook.ru/arbitrage/tracking/events/documents'
    case_source_api_url: str = 'http://185.47.206.115:8081'
    case_source_timeout_seconds: float = 20.0
    casebook_api_key: str
    casebook_api_version: str = '2'
    casebook_auth_scheme: str = 'auto'
    page_size: int = 100
    casebook_retry_attempts: int = 8
    casebook_retry_base_delay_seconds: float = 2.0
    casebook_retry_max_delay_seconds: float = 60.0
    progress_log_every_items: int = 500

    scheduler_interval_hours: int = 2
    scheduler_run_on_startup: bool = True

    full_sync_secret: str | None = None


settings = Settings()
