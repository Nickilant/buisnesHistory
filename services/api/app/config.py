from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'casebook-api'
    database_url: str = 'postgresql+psycopg2://app:app@postgres:5432/casebook'

    jwt_secret: str = 'change-me'
    jwt_algorithm: str = 'HS256'
    jwt_exp_minutes: int = 720
    allow_local_dev_auth: bool = True
    cors_allow_origins: str = '*'

    frontend_url: str = 'http://localhost:8080'


settings = Settings()
