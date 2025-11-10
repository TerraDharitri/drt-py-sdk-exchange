from .base import BaseEnvironmentSettings


class DevnetSettings(BaseEnvironmentSettings):
    """Devnet environment settings."""

    # Override defaults for devnet
    DEFAULT_PROXY: str = "https://devnet-gateway.dharitri.org"
    DEFAULT_API: str = "https://devnet-api.dharitri.org"
    GRAPHQL: str = "https://devnet-graph.dharitrix.com/graphql"

    DEFAULT_OWNER: str = "wallets/devnet-dex-owner.pem"
    DEFAULT_ADMIN: str = "wallets/devnet-dex-owner.pem"

    DEFAULT_CONFIG_SAVE_PATH: str = "deploy/configs-devnet"
