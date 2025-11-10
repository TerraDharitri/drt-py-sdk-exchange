from .base import BaseEnvironmentSettings


class ChainSimSettings(BaseEnvironmentSettings):
    """Chain simulator environment settings."""

    # Override defaults for chain simulator
    DEFAULT_PROXY: str = "http://localhost:8085"
    DEFAULT_API: str = "http://localhost:3001"
    GRAPHQL: str = "https://graph.dharitrix.com/graphql"

    DEFAULT_OWNER: str = "wallets/C1.pem"
    DEFAULT_ADMIN: str = "wallets/C1.pem"

    DEFAULT_CONFIG_SAVE_PATH: str = "deploy/configs-mainnet"

    SF_DEX_REFERENCE_ADDRESS: str = "drt1qqqqqqqqqqqqqpgqq66xk9gfr4esuhem3jru86wg5hvp33a62jpsh4nhal"
    DEX_OWNER_ADDRESS: str = "drt1ss6u80ruas2phpmr82r42xnkd6rxy40g9jl69frppl4qez9w2jpsaws9xq"
    DEX_ADMIN_ADDRESS: str = "drt1ss6u80ruas2phpmr82r42xnkd6rxy40g9jl69frppl4qez9w2jpsaws9xq"
    SHADOWFORK_FUNDING_ADDRESS: str = "drt1rf4hv70arudgzus0ymnnsnc4pml0jkywg2xjvzslg0mz4nn2tg7qr2cgel"
