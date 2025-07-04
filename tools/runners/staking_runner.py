from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
import json
import os
from time import sleep
from typing import Any
from dharitri_py_sdk import Address
from dharitri_py_sdk.core.transaction_builders import ContractCallBuilder, \
    DefaultTransactionBuildersConfiguration
from config import GRAPHQL
from contracts.contract_identities import StakingContractVersion
from contracts.staking_contract import StakingContract
from tools.common import API, OUTPUT_FOLDER, OUTPUT_PAUSE_STATES, \
    PROXY, fetch_and_save_contracts, fetch_contracts_states, \
    fetch_new_and_compare_contract_states, get_owner, \
    get_saved_contract_addresses, get_user_continue, run_graphql_query
from tools.runners.common_runner import add_generate_transaction_command, \
    add_upgrade_command, fund_shadowfork_accounts, \
    get_acounts_with_token, get_default_signature, read_accounts_from_json, \
    sync_account_nonce, verify_contracts, add_verify_command
from tools.runners.metastaking_runner import get_metastaking_addresses_from_chain
from utils.contract_data_fetchers import StakingContractDataFetcher
from utils.utils_chain import Account, WrapperAddress
from utils.utils_generic import split_to_chunks, get_file_from_url_or_path
from utils.utils_tx import DCDTToken, NetworkProviders
import config

from contracts.simple_lock_energy_contract import SimpleLockEnergyContract

from context import Context

from utils.utils_chain import get_bytecode_codehash, log_explorer_transaction
from tools.runners.common_config import STAKING_BOOSTED_REWARDS_PERCENTAGE, STAKING_BOOSTED_YIELD_FACTORS


STAKINGS_LABEL = "stakings"
OUTPUT_STAKING_CONTRACTS_FILE = OUTPUT_FOLDER / "staking_data.json"


def setup_parser(subparsers: ArgumentParser) -> ArgumentParser:
    """Set up argument parser for staking commands"""
    group_parser = subparsers.add_parser('stakings', help='stakings group commands')
    subgroup_parser = group_parser.add_subparsers()

    contract_parser = subgroup_parser.add_parser('contract', help='stakings contract commands')

    contract_group = contract_parser.add_subparsers()
    add_upgrade_command(contract_group, upgrade_staking_contracts)
    add_verify_command(contract_group, verify_staking_contracts)

    command_parser = contract_group.add_parser('fetch-all', help='fetch all contracts command')
    command_parser.set_defaults(func=fetch_and_save_stakings_from_chain)

    command_parser = contract_group.add_parser('pause-all', help='pause all contracts command')
    command_parser.set_defaults(func=pause_all_staking_contracts)

    command_parser = contract_group.add_parser('resume-all', help='resume all contracts command')
    command_parser.set_defaults(func=resume_all_staking_contracts)

    command_parser = contract_group.add_parser('setup-boost-all', help='setup boost for all contracts command')
    command_parser.add_argument('--compare-states', action='store_true',
                        help='compare states before and after the setup')
    command_parser.set_defaults(func=setup_boosted_parameters_for_all_stakings)

    command_parser = contract_group.add_parser('pause', help='pause contract command')
    command_parser.add_argument('--address', type=str, help='contract address')
    command_parser.set_defaults(func=pause_staking_contract)

    command_parser = contract_group.add_parser('resume', help='resume contract command')
    command_parser.add_argument('--address', type=str, help='contract address')
    command_parser.set_defaults(func=resume_staking_contract)

    command_parser = contract_group.add_parser('setup-boost', help='setup boost for specific contract command')
    command_parser.add_argument('--compare-states', action='store_true',
                        help='compare states before and after the setup')
    command_parser.add_argument('--address', type=str, help='contract address')
    command_parser.set_defaults(func=setup_boosted_parameters_for_staking)

    command_parser = contract_group.add_parser('produce-rewards', help='toggle rewards distribution command')
    command_parser.add_argument('--state', action='store_true', help='rewards distribution state')
    group = command_parser.add_mutually_exclusive_group()
    group.add_argument('--address', type=str, help='contract address')
    group.add_argument('--all', action='store_true', help='run command for all contracts')
    command_parser.set_defaults(func=produce_staking_rewards)

    command_parser = contract_group.add_parser('unbond-epochs', help='set minim unbond epoochs command')
    command_parser.add_argument('--epochs', type=int, help='minim unbond epochs')
    group = command_parser.add_mutually_exclusive_group()
    group.add_argument('--address', type=str, help='contract address')
    group.add_argument('--all', action='store_true', help='run command for all contracts')
    command_parser.set_defaults(func=set_unbond_epochs)

    transactions_parser = subgroup_parser.add_parser('generate-transactions', help='metastaking transactions commands')

    transactions_group = transactions_parser.add_subparsers()
    add_generate_transaction_command(transactions_group, generate_unstake_tokens_transactions, 'unstakeTokens', 'unstake tokens command')
    add_generate_transaction_command(transactions_group, generate_unbond_tokens_transactions, 'unbondTokens', 'unbond tokens command')

    return group_parser


def fetch_and_save_stakings_from_chain(_):
    """Fetch staking contracts from chain"""

    print("Fetch staking contracts from chain")

    stakings = get_staking_addresses_from_chain()
    print(f"Retrieved {len(stakings)} staking contracts.")
    fetch_and_save_contracts(stakings, STAKINGS_LABEL, OUTPUT_STAKING_CONTRACTS_FILE)


def pause_staking_contracts(staking_addresses: list[str]):
    """Pause staking contracts"""

    print("Pause staking contracts")

    network_providers = NetworkProviders(API, PROXY)
    dex_owner = get_owner(network_providers.proxy)

    # pause all the stakings
    count = 1
    for staking_address in staking_addresses:
        print(f"Processing contract {count} / {len(staking_addresses)}: {staking_address}")
        data_fetcher = StakingContractDataFetcher(Address.new_from_bech32(staking_address), network_providers.proxy.url)
        contract_state = data_fetcher.get_data("getState")
        contract = StakingContract("", 0, 0, 0, StakingContractVersion.V1, "", staking_address)
        if contract_state != 0:
            tx_hash = contract.pause(dex_owner, network_providers.proxy)
            if not network_providers.check_simple_tx_status(tx_hash, f"pause staking contract: {staking_address}"):
                if not get_user_continue(config.FORCE_CONTINUE_PROMPT):
                    return
        else:
            print(f"Contract {staking_address} already inactive. Current state: {contract_state}")

        count += 1


def pause_all_staking_contracts(_):
    staking_addresses = get_all_staking_addresses()
    pause_staking_contracts(staking_addresses)


def pause_staking_contract(args: Any):
    staking_address = args.address
    pause_staking_contracts([staking_address])


def resume_staking_contracts(staking_addresses: list[str]):
    """Resume staking contracts"""

    print("Resume staking contracts")

    network_providers = NetworkProviders(API, PROXY)
    dex_owner = get_owner(network_providers.proxy)

    if not os.path.exists(OUTPUT_PAUSE_STATES):
        print("Contract initial states not found!"
              "Cannot proceed safely without altering initial state.")

    with open(OUTPUT_PAUSE_STATES, encoding="UTF-8") as reader:
        contract_states = json.load(reader)

    # pause all the staking contracts
    count = 1
    for staking_address in staking_addresses:
        print(f"Processing contract {count} / {len(staking_addresses)}: {staking_address}")
        if staking_address not in contract_states:
            print(f"Contract {staking_address} wasn't touched for no available initial state!")
            continue
        # resume only if the staking contract was active
        if contract_states[staking_address] == 1:
            contract = StakingContract("", 0, 0, 0, StakingContractVersion.V1, "", staking_address)
            tx_hash = contract.resume(dex_owner, network_providers.proxy)
            if not network_providers.check_simple_tx_status(tx_hash, f"resume staking contract: {staking_address}"):
                if not get_user_continue(config.FORCE_CONTINUE_PROMPT):
                    return
        else:
            print(f"Contract {staking_address} wasn't touched because of initial state: "
                  f"{contract_states[staking_address]}")

        count += 1


def resume_all_staking_contracts(_):
    staking_addresses = get_all_staking_addresses()
    resume_staking_contracts(staking_addresses)


def resume_staking_contract(args: Any):
    staking_address = args.address
    resume_staking_contracts([staking_address])


def upgrade_staking_contracts(args: Any):
    """Upgrade staking contracts"""

    compare_states = args.compare_states

    staking_addresses = get_staking_addresses_from_chain()
    if not args.all:
        staking_addresses = [args.address]

    network_providers = NetworkProviders(API, PROXY)
    dex_owner = get_owner(network_providers.proxy)

    if not staking_addresses:
        print("No staking contracts available!")
        return
    
    print(f"Processing {len(staking_addresses)} staking contracts.")
    
    if args.bytecode:
        bytecode_path = get_file_from_url_or_path(args.bytecode)
    else:
        bytecode_path = get_file_from_url_or_path(config.STAKING_V3_BYTECODE_PATH)

    print(f"New bytecode codehash: {get_bytecode_codehash(bytecode_path)}")
    if not get_user_continue(config.FORCE_CONTINUE_PROMPT):
        return

    if compare_states:
        print("Fetching contracts states before upgrade...")
        fetch_contracts_states("pre", network_providers, staking_addresses, STAKINGS_LABEL)

        if not get_user_continue():
            return

    count = 1
    for staking_address in staking_addresses:
        print(f"Processing contract {count} / {len(staking_addresses)}: {staking_address}")

        staking_contract = StakingContract.load_contract_by_address(staking_address, StakingContractVersion.V3Boosted)

        staking_contract.version = StakingContractVersion.V3Boosted
        tx_hash = staking_contract.contract_upgrade(dex_owner, network_providers.proxy, bytecode_path, 
                                                    [], True)

        if not network_providers.check_simple_tx_status(tx_hash, f"upgrade staking contract: {staking_address}"):
            if not get_user_continue():
                return

        if compare_states:
            fetch_new_and_compare_contract_states(STAKINGS_LABEL, staking_address, network_providers)

        if not get_user_continue():
            return

        count += 1


def verify_staking_contracts(args: Any):
    print("Verifying staking contracts...")

    all_addresses = get_staking_addresses_from_chain()
    verify_contracts(args, all_addresses)
    
    print("All contracts have been verified.")
    

def setup_boosted_parameters_with_energy_address(staking_addresses: list[str], energy_address: str, compare_states: bool = False):
    """Setup boosted parameters for staking contract with provided energy address"""

    network_providers = NetworkProviders(API, PROXY)
    dex_owner = get_owner(network_providers.proxy)

    energy_contract = SimpleLockEnergyContract("", "", "", "", energy_address)
    count = 1

    for staking_address in staking_addresses:
        print(f"Processing contract {count} / {len(staking_addresses)}: {staking_address}")

        if compare_states:
            print("Fetching contracts states before setup...")
            fetch_contracts_states("pre", network_providers, [staking_address], STAKINGS_LABEL)

            if not get_user_continue():
                return

        staking_contract = StakingContract("", 0, 0, 0, StakingContractVersion.V3Boosted, "", staking_address)

        hashes = []
        hashes.append(staking_contract.set_boosted_yields_rewards_percentage(dex_owner, network_providers.proxy, STAKING_BOOSTED_REWARDS_PERCENTAGE))
        hashes.append(staking_contract.set_boosted_yields_factors(dex_owner, network_providers.proxy, STAKING_BOOSTED_YIELD_FACTORS))
        hashes.append(staking_contract.set_energy_factory_address(dex_owner, network_providers.proxy, energy_address))
        hashes.append(energy_contract.add_sc_to_whitelist(dex_owner, network_providers.proxy, staking_address))
            
        sleep(6)

        for tx_hash in hashes:
            if not network_providers.check_simple_tx_status(tx_hash, f"boosted parameters for: {staking_address}"):
                if not get_user_continue():
                    return

        if compare_states:
            fetch_new_and_compare_contract_states(STAKINGS_LABEL, staking_address, network_providers)

            if not get_user_continue():
                return
        
        count += 1
    

def setup_boosted_parameters_for_staking(args: Any):
    """Setup boosted parameters for staking contract"""
    print("Setup boosted parameters for staking contract")
    staking_address = args.address
    compare_states = args.compare_states
    if not staking_address:
        print("Staking address is required!")
        return

    context = Context()
    energy_contract = context.get_contracts(config.SIMPLE_LOCKS_ENERGY)[0]

    setup_boosted_parameters_with_energy_address([staking_address], energy_contract.address, compare_states)


def setup_boosted_parameters_for_all_stakings(args: Any):
    """Setup boosted parameters for all staking contracts"""
    print("Setup boosted parameters for all staking contracts")
    compare_states = args.compare_states

    context = Context()
    energy_contract = context.get_contracts(config.SIMPLE_LOCKS_ENERGY)[0]
    staking_addresses = get_all_staking_addresses()

    setup_boosted_parameters_with_energy_address(staking_addresses, energy_contract.address, compare_states)


def produce_staking_rewards(args: Any):
    """Toggle produce rewards on staking contract"""

    state = args.state
    staking_addresses = get_staking_addresses_from_chain()
    if not args.all:
        staking_addresses = [args.address]

    network_providers = NetworkProviders(API, PROXY)
    dex_owner = get_owner(network_providers.proxy)

    for staking_address in staking_addresses:
        print(f"Toggle rewards distribution on staking contract: {staking_address}")

        staking_contract = StakingContract.load_contract_by_address(staking_address, StakingContractVersion.V3Boosted)
        if state:
            staking_contract.start_produce_rewards(dex_owner, network_providers.proxy)
        else:
            staking_contract.end_produce_rewards(dex_owner, network_providers.proxy)


def set_unbond_epochs(args: Any):
    """Set unbond epochs on staking contract"""

    if args.epochs is None:
        print("Missing required arguments!")
        return

    epochs = args.epochs
    staking_addresses = get_staking_addresses_from_chain()
    if not args.all:
        staking_addresses = [args.staking_address]

    network_providers = NetworkProviders(API, PROXY)
    dex_owner = get_owner(network_providers.proxy)

    for staking_address in staking_addresses:
        print(f"Toggle rewards distribution on staking contract: {staking_address}")

        staking_contract = StakingContract.load_contract_by_address(staking_address, StakingContractVersion.V3Boosted)
        staking_contract.set_unbond_epochs(dex_owner, network_providers.proxy, epochs)


def generate_unstake_tokens_transactions(args: Any):
    """Generate unstake tokens transactions"""

    if not hasattr(args, 'unbond_tokens'):
        args.unbond_tokens = False

    staking_address = args.address
    exported_accounts_path = args.accounts_export
    function_name = "unstakeFarm" if not args.unbond_tokens else "unbondFarm"

    if not exported_accounts_path:
        print("Missing required arguments!")
        return

    if not staking_address and not args.all:
        print("Missing required arguments!")
        return

    network_providers = NetworkProviders(API, PROXY)
    network_providers.network = network_providers.proxy.get_network_config()
    chain_id = network_providers.proxy.get_network_config().chain_id
    config_tx = DefaultTransactionBuildersConfiguration(chain_id=chain_id)
    signature = get_default_signature()
    default_account = Account(None, config.DEFAULT_OWNER)
    default_account.sync_nonce(network_providers.proxy)

    exported_accounts = read_accounts_from_json(exported_accounts_path)

    fund_shadowfork_accounts(exported_accounts)
    sleep(30)

    metastaking_addresses = get_metastaking_addresses_from_chain()

    staking_addresses = get_staking_addresses_from_chain()
    if not args.all:
        staking_addresses = [staking_address]

    with ThreadPoolExecutor(max_workers=500) as executor:
        exported_accounts = list(executor.map(sync_account_nonce, exported_accounts))

    for staking_address in staking_addresses:
        staking_contract = StakingContract.load_contract_by_address(staking_address, StakingContractVersion.V3Boosted)
        accounts_with_token = get_acounts_with_token(exported_accounts, staking_contract.farm_token)

        print(f"Found {len(accounts_with_token)} accounts with token {staking_contract.farm_token}")

        transactions = []
        accounts_index = 1
        for account_with_token in accounts_with_token:
            print(f"Processing account {accounts_index} / {len(accounts_with_token)}")

            account = Account(account_with_token.address, config.DEFAULT_OWNER)
            account.address = WrapperAddress.from_bech32(account_with_token.address)
            account.nonce = account_with_token.nonce

            tokens = [
                token for token in account_with_token.account_tokens_supply
                if token.token_name == staking_contract.farm_token and (
                    (len(token.attributes) > 13 and not args.unbond_tokens) or (len(token.attributes) < 13 and args.unbond_tokens)
                )
            ]

            print(f"Found {len(tokens)} tokens to unstake for account {account_with_token.address}")

            for token in tokens:
                payment_tokens = [DCDTToken(token.token_name, int(token.token_nonce_hex, 16), int(token.supply)).to_token_payment()]
                if not account.address.is_smart_contract():
                    builder = ContractCallBuilder(
                        config=config_tx,
                        contract=Address.new_from_bech32(staking_address),
                        function_name=function_name,
                        caller=account.address,
                        call_arguments=[],
                        value=0,
                        gas_limit=13000000,
                        nonce=account.nonce,
                        dcdt_transfers=payment_tokens
                    )

                    tx = builder.build()
                    tx.signature = signature

                    transactions.append(tx)
                    account.nonce += 1
                elif account.address.to_bech32() not in metastaking_addresses:
                    builder = ContractCallBuilder(
                        config=config_tx,
                        contract=account.address,
                        function_name="callInternalTransferEndpoint",
                        caller=default_account.address,
                        call_arguments=[
                            token.token_name,
                            int(token.token_nonce_hex, 16),
                            int(token.supply),
                            Address.new_from_bech32(staking_address),
                            function_name,
                        ],
                        value=0,
                        gas_limit=50000000,
                        nonce=default_account.nonce
                    )
                    default_account.nonce += 1
                    tx = builder.build()
                    tx.signature = signature
                    transactions.append(tx)

            index = exported_accounts.index(account_with_token)
            exported_accounts[index].nonce = account.nonce
            accounts_index += 1

        transactions_chunks = split_to_chunks(transactions, 100)
        for chunk in transactions_chunks:
            network_providers.proxy.send_transactions(chunk)


def generate_unbond_tokens_transactions(args: Any):
    """Generate unbond tokens transactions"""

    args.unbond_tokens = True

    generate_unstake_tokens_transactions(args)


def get_staking_addresses_from_chain() -> list:
    """Get staking addresses from chain"""

    query = """
        { stakingFarms { address } }
        """

    result = run_graphql_query(GRAPHQL, query)

    address_list = []
    for entry in result['data']['stakingFarms']:
        address_list.append(entry['address'])

    return address_list


def get_all_staking_addresses() -> list:
    """Get all staking addresses"""

    return get_saved_contract_addresses(STAKINGS_LABEL, OUTPUT_STAKING_CONTRACTS_FILE)