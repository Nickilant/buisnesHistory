from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'casebook-updater'
    database_url: str = 'postgresql+psycopg2://app:app@postgres:5432/casebook'

    casebook_api_url: str = 'https://api3.casebook.ru/arbitrage/tracking/events/documents'
    casebook_api_key: str
    casebook_api_version: str = '2'
    casebook_auth_scheme: str = 'auto'
    page_size: int = 100

    scheduler_hour_msk: int = 11
    scheduler_minute_msk: int = 59


settings = Settings()
