import concurrent.futures
import logging
import random
import sys
import time
import traceback
from itertools import count
from typing import List, Optional
from argparse import ArgumentParser

import config
from contracts.rewa_wrap_contract import RewaWrapContract
from utils.logger import get_logger
from context import Context
from contracts.pair_contract import PairContract, AddLiquidityEvent, SwapFixedInputEvent
from events.event_generators import (generate_add_initial_liquidity_event,
                                                       generate_add_liquidity_event,
                                                       generateEnterFarmEvent,
                                                       generateEnterMetastakeEvent,
                                                       generateClaimMetastakeRewardsEvent,
                                                       generateExitMetastakeEvent,
                                                       generateExitFarmEvent)
from utils.contract_data_fetchers import PairContractDataFetcher
from utils.utils_generic import log_step_pass, log_step_fail
from ported_arrows.stress.send_token_from_minter import main as send_token_from_minter
from ported_arrows.stress.send_rewa_from_minter import main as send_rewa_from_minter
from utils.utils_chain import Account, WrapperAddress as Address, nominated_amount


logger = get_logger(__name__)


def main(cli_args: List[str]):
    parser = ArgumentParser()
    parser.add_argument("--threads", required=False, default="3")  # number of concurrent threads to execute operations
    parser.add_argument("--repeats", required=False, default="0")  # number of total operations to execute; 0 - infinite
    parser.add_argument("--skip-swaping", action="store_true", default=False)
    parser.add_argument("--skip-minting", action="store_true", default=False)
    args = parser.parse_args(cli_args)

    context = Context()

    # add initial liquidity in pair contracts if necessary
    add_initial_liquidity(context)

    if not args.skip_minting:
        # send rEWA for fees to users
        send_rewa(context)
        wait_time = 40
        logger.info(f"rEWA account minting done. Waiting for the dust to settle: {wait_time}s")
        time.sleep(wait_time)

    if not args.skip_swaping:
        # swap rEWA in minter for DEX tokens
        swap_tokens(context)
        wait_time = 12
        logger.info(f"Swapping tokens done. Waiting for the dust to settle: {wait_time}s")
        time.sleep(wait_time)

    if not args.skip_minting:
        # send DEX tokens to users
        send_tokens(context)
        wait_time = 40
        logger.info(f"Minting accounts with DEX tokens done. "
                    f"Waiting for the dust to settle: {wait_time}s until execution start")
        time.sleep(wait_time)

    create_nonce_file(context)

    # stress generator for adding liquidity, enter farm, enter metastaking, claim metastaking, exit metastaking
    stress(context, int(args.threads), int(args.repeats))


def create_nonce_file(context: Context):
    context.accounts.sync_nonces(context.network_provider.proxy)
    context.accounts.store_nonces(context.nonces_file)
    context.deployer_account.sync_nonce(context.network_provider.proxy)


def swap_tokens(context: Context):
    max_rewa_to_spend = nominated_amount(20)

    rewa_on_purse = context.network_provider.proxy.get_account(context.admin_account.address).balance
    rewa_to_spend = min(rewa_on_purse - nominated_amount(1), max_rewa_to_spend)
    rewa_to_swap = rewa_to_spend // 2
    logger.info(f"Swapping {rewa_to_swap} rEWA for wREWA and {rewa_to_swap} rEWA for tokens.")

    wrap_contracts = context.get_contracts(config.REWA_WRAPS)
    wrap_contract: Optional[RewaWrapContract] = None
    for wrap_contract in wrap_contracts:
        if Address(wrap_contract.address).get_shard() == context.admin_account.address.get_shard():
            break

    if wrap_contract is None:
        log_step_fail("No wrap contract found.")
        sys.exit(1)

    # swap all spendable rEWA for wREWA
    tx_hash = wrap_contract.wrap_rewa(context.admin_account, context.network_provider.proxy, rewa_to_spend)
    context.network_provider.check_simple_tx_status(tx_hash)

    # swap half of the wREWA for tokens
    pair_contracts = context.get_contracts(config.PAIRS_V2)
    rewa_per_pair = rewa_to_swap // len(pair_contracts)
    pair_contract: Optional[PairContract] = None
    for pair_contract in pair_contracts:
        swapped_token = pair_contract.firstToken if wrap_contract.wrapped_token == pair_contract.secondToken else \
            pair_contract.secondToken
        swap_event = SwapFixedInputEvent(tokenA=wrap_contract.wrapped_token,
                                         amountA=rewa_per_pair,
                                         tokenB=swapped_token,
                                         amountBmin=1)
        logger.info(f"Swapping {rewa_per_pair} {wrap_contract.wrapped_token} for {swapped_token} "
                    f"in pair {pair_contract.address}.")
        tx_hash = pair_contract.swap_fixed_input(context.network_provider, context.admin_account, swap_event)
        context.network_provider.check_simple_tx_status(tx_hash)


def send_rewa(context: Context):
    proxy_url = config.DEFAULT_PROXY
    accounts = config.DEFAULT_ACCOUNTS
    minter = config.DEFAULT_ADMIN
    amount = nominated_amount(1)

    logger.info(f"Funding each account with {amount} rEWA.")
    args = [f'--proxy={proxy_url}', f'--accounts={accounts}',
            f'--minter={minter}', f'--value-atoms={amount}']
    send_rewa_from_minter(args)
    time.sleep(7)
    context.admin_account.sync_nonce(context.network_provider.proxy)


def send_tokens(context: Context):
    proxy_url = config.DEFAULT_PROXY
    accounts = config.DEFAULT_ACCOUNTS
    minter = config.DEFAULT_ADMIN

    for token in context.deploy_structure.tokens:
        amount_on_deployer = context.network_provider.proxy.get_fungible_token_of_account(
            context.admin_account.address, token).balance
        
        if amount_on_deployer == 0:
            continue

        # divide the amount between all user accounts + deployer
        no_accounts = len(context.accounts.accounts) + 1
        amount = amount_on_deployer // no_accounts
        logger.info(f"Funding {no_accounts} accounts with {amount} {token} tokens each "
                    f"from a total of {amount_on_deployer}.")
        args = [f'--proxy={proxy_url}', f'--accounts={accounts}',
                f'--minter={minter}', f'--token={token}', f'--amount-atoms={amount}']
        send_token_from_minter(args)
        time.sleep(7)
    
    context.admin_account.sync_nonce(context.network_provider.proxy)


def add_initial_liquidity(context: Context):
    # add initial liquidity
    pair_contract: PairContract
    for pair_contract in context.get_contracts(config.PAIRS_V2):
        pair_data_fetcher = PairContractDataFetcher(Address(pair_contract.address), context.network_provider.proxy.url)
        first_token_liquidity = pair_data_fetcher.get_token_reserve(pair_contract.firstToken)
        if first_token_liquidity == 0:
            event = AddLiquidityEvent(
                pair_contract.firstToken, nominated_amount(10), 1,
                pair_contract.secondToken, nominated_amount(10), 1
            )
            pair_contract.add_initial_liquidity(context.network_provider, context.deployer_account, event)
            time.sleep(6)


def stress(context: Context, threads: int, repeats: int):
    deployer_shard = context.deployer_account.address.get_shard()
    accounts = context.accounts.get_in_shard(deployer_shard)

    context.swap_min_tokens_to_spend = 0.0001
    context.swap_max_tokens_to_spend = 0.001

    if threads == 1:
        """Sequential run"""
        for _ in range(repeats if repeats != 0 else 99999999):
            stress_per_account(context, random.choice(accounts))
    elif threads > 1:
        """ Concurential run """
        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
            jobs = min(threads, repeats) if repeats != 0 else threads
            finished_jobs = 0
            futures = [executor.submit(stress_per_account, context, random.choice(accounts))
                       for _ in range(jobs)]

            # feed the thread workers as they complete each job
            while finished_jobs < repeats or repeats == 0:
                try:
                    for future in concurrent.futures.as_completed(futures):
                        finished_jobs += 1
                        log_step_pass(f"Finished {finished_jobs} repeats.")
                        futures.remove(future)
                        # spawn a new job
                        futures.append(executor.submit(stress_per_account, context, random.choice(accounts)))
                        if future.exception() is not None:
                            log_step_fail(f"Thread failed: {future.exception()}")
                except Exception as ex:
                    traceback.print_exception(*sys.exc_info())
                    log_step_fail(f"Something failed: {ex}")
    else:
        log_step_fail(f"Number of threads must be minimum 1!")
        return


def stress_per_account(context: Context, account: Account):
    min_time = 2
    max_time = 10
    deployer_shard = context.deployer_account.address.get_shard()
    sleep_time = config.CROSS_SHARD_DELAY if account.address.get_shard() is not deployer_shard \
        else config.INTRA_SHARD_DELAY

    account.sync_nonce(context.network_provider.proxy)

    for metastaking_contract in context.get_contracts(config.METASTAKINGS_V2):
        farm_contract = context.get_farm_contract_by_address(metastaking_contract.farm_address)
        pair_contract = context.get_pair_contract_by_address(metastaking_contract.lp_address)

        operation_hit = False

        for _ in range(1):
            if random.randint(0, 100) <= 40:    # 40% chance
                if generate_add_liquidity_event(context, account, pair_contract):
                    time.sleep(sleep_time)
                    operation_hit = True
                if generateEnterFarmEvent(context, account, farm_contract):
                    time.sleep(sleep_time)
                    operation_hit = True
                if generateEnterMetastakeEvent(context, account, metastaking_contract):
                    time.sleep(sleep_time)
                    operation_hit = True
            if random.randint(0, 100) <= 70:    # 70% chance
                if generateClaimMetastakeRewardsEvent(context, account, metastaking_contract):
                    time.sleep(sleep_time)
                    operation_hit = True
            if random.randint(0, 100) <= 20:    # 20% chance
                if generateExitMetastakeEvent(context, account, metastaking_contract):
                    time.sleep(sleep_time)
                    operation_hit = True

        if operation_hit:
            wait_time = random.randint(min_time, max_time)
            logger.info(f'Finished repeat execution. Waiting for {wait_time}s.')
            time.sleep(wait_time)


if __name__ == "__main__":
    main(sys.argv[1:])
