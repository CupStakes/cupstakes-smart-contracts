from pyteal import *
from assets import ticket_price, burn_ticket_price, storage_app_id, oracle_app_id, rewards_pool_address, super_admin_address

# Of the above:
# - immutable:
#   - rewards_pool_address: hardcoded
#   - storage_app_id: hardcoded
# - mutable / just initial values, stored in global storage:
#   - ticket_price
#   - burn_ticket_price
#   - oracle_app_id

storage_app_id_int = Int(storage_app_id)

max_randomness_range = 1000

# Define Byte sequences used
# Numbers and special symbols
bytes_empty = Bytes('')
bytes_151f7c75 = Bytes('base16', '151f7c75') # successful return from ABI call header

# global storage lookup keys
# kill switch: leave only "collect" action available
kill_switch_key = Bytes('kill')
# free draw NFT ID + implicit availability 
free_draw_nft_id_key = Bytes('free_draw_nft')
# ticket price in mAlgo
ticket_key = Bytes('ticket')
burn_ticket_key = Bytes('burn_ticket')
# oracle app id
oracle_app_id_key = Bytes('oracle_app_id')
# sum or all odds of team NFTs. MUST BE POWER OF TWO
max_odds_key = Bytes('max_odds')
# randomness oracle maximum range
max_randomness_range_key = Bytes('max_randomness_range')

# local (user) storage lookup keys
# NFT slots, available to collect
slot1_key = Bytes("slot1")
slot2_key = Bytes("slot2")
slot3_key = Bytes("slot3")
# queued draw action. valid after round
draw_round_key = Bytes("draw_round")
# queued draw action. amount to draw
draw_amount_key = Bytes("draw_amount")
draw_amount_paid_key = Bytes("draw_amount_paid")

# Log and misc
bytes_rand = Bytes('rand')
bytes_ret = Bytes('ret')
bytes_default = Bytes('default')
bytes_rand_mapped = Bytes("Rand mapped")

# extra_fields{} for zero fees
zero_fee_extra_fields={}
zero_fee_extra_fields[TxnField.fee]=Int(0)

# error strings end up in algod error messages <3
err_contract_killed = "CONTRACT KILLED"
err_validation = "VALIDATION FAIL"
err_payment_incorrect = "PAYMENT FAIL"
err_payment_amount_invalid = "PAYMENT AMT FAIL"
err_wait_for_randomness = "WAIT FOR RANDOMNESS"
err_oracle_invalid = "ORACLE INVALID"
err_randomness_fail = "RANDOMNESS FAIL"
err_drawing_failed = "DRAWING FAILED"
err_drawing_disabled = "DRAWING DISABLED"
err_slot_not_empty = "MUST COLLECT"
err_no_free_slot = "NO FREE SLOT"
err_no_slots_full = "NO NFTs IN SLOTS"
err_unauthorized = "UNAUTH"
err_draw_queued = "ERR DRAW QUEUED ALREADY"
err_no_draw_queued = "ERR NO DRAW QUEUED"
err_randomness_expired = "ERR RANDOMNESS EXPIRED"
err_randomness_not_expired = "ERR RANDOMNESS NOT EXPIRED"
err_no_burn_available = "ERR BURN NOT AVAILABLE"
err_invalid_slot = "ERR INVALID SLOT"
err_no_burn_hacking = "ERR NO BURN HACKING"

opup = OpUp(OpUpMode.Explicit, storage_app_id_int)

# my greatest invention
# assert that fails with an error string attached
# Example error message: logic eval error: assert failed pc=2330. Details: pc=2330, opcodes=pushbytes 0x45525220445241572051554555454420414c5245414459 // "ERR DRAW QUEUED ALREADY"
def custom_assert(cond, str):
    return If(Not(cond)).Then(Assert(Bytes('') == Bytes(str)))

# same as above, but inversed - skips a Not()
# not sure if it saves opcode costs
def fail_if(cond, str):
    return If(cond).Then(Assert(Bytes('') == Bytes(str)))

# as above but without condition
def fail(str):
    return Assert(bytes_empty == Bytes(str))

# helper function to get the draw amount of a user
# called with 0 for txn sender (draw/freedraw) or 1 (first Txn.accounts entry) for executing the draw 
def user_draw_amount(acctIdx):
    return App.localGet(acctIdx, draw_amount_key)

# return whether draw_round randomness has expired for user $acct
# called with 1 (first Txn.accounts entry) for executing the draw or refund
def randomness_expired(addrIdx):
    return Global.round() > App.globalGet(max_randomness_range_key) + App.localGet(addrIdx, draw_round_key)

# reset user draw state to initial (no draw)
# called with 0 when opting in or 1 (first Txn.accounts entry) for executing the draw 
def reset_user_draw_state(addrIdx):
    return Seq(
        App.localPut(addrIdx, draw_amount_key, Int(0)),
        App.localPut(addrIdx, draw_amount_paid_key, Int(0)),
        App.localPut(addrIdx, draw_round_key, Int(0))
    )

# Set up default values upon creation; convenience operation, all of these are updatable
handle_creation = Seq(
    App.globalPut(kill_switch_key, Int(0)), # kill-switch when 1 - only allow collecting and emptying NFTs
    App.globalPut(free_draw_nft_id_key, Int(0)), # free draw NFT ID
    App.globalPut(ticket_key, Int(ticket_price)), # ticket price in mAlgo
    App.globalPut(burn_ticket_key, Int(burn_ticket_price)), # burn ticket price in mAlgo
    App.globalPut(max_odds_key, Int(1048576)), # max odds for modulo op. MUST BE POWER OF TWO
    App.globalPut(oracle_app_id_key, Int(oracle_app_id)), # randomness oracle app id - mutable in case of permanent beacon failure
    App.globalPut(max_randomness_range_key, Int(max_randomness_range)), # range after which to refund ticket price
    Approve()
)

# User opting in: Set up 3 local storage slots representing drawn NFT ID load slots,
# and the "draw queue" entries: amount of draws and round to draw at
handle_optin = Seq(
    App.localPut(Int(0), slot1_key, Int(0)),
    App.localPut(Int(0), slot2_key, Int(0)),
    App.localPut(Int(0), slot3_key, Int(0)),
    reset_user_draw_state(Int(0)),
)

# Collect available NFTs
# Assumes we have "infinite" NFTs available - will mint 10M per for CupStakes
@Subroutine(TealType.none)
def sub_collect():
    return Seq(
        # check that at least some NFT slots are full
        custom_assert(Or(
            App.localGet(Int(0), slot1_key) != Int(0),
            App.localGet(Int(0), slot2_key) != Int(0),
            App.localGet(Int(0), slot3_key) != Int(0)
        ), err_no_slots_full),
        If(App.localGet(Int(0), slot1_key) != Int(0)).Then(Seq(
            # send slot 1
            InnerTxnBuilder.Execute({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: App.localGet(Int(0), slot1_key),
                TxnField.asset_receiver: Txn.sender(),
                TxnField.asset_amount: Int(1),
                TxnField.fee: Int(0)
            }),
            # clear slot 1
            App.localPut(Int(0), slot1_key, Int(0))
        )),
        If(App.localGet(Int(0), slot2_key) != Int(0)).Then(Seq(
            # send slot 2
            InnerTxnBuilder.Execute({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: App.localGet(Int(0), slot2_key),
                TxnField.asset_receiver: Txn.sender(),
                TxnField.asset_amount: Int(1),
                TxnField.fee: Int(0)
            }),
            # clear slot 2
            App.localPut(Int(0), slot2_key, Int(0))
        )),
        If(App.localGet(Int(0), slot3_key) != Int(0)).Then(Seq(
            # send slot 3
            InnerTxnBuilder.Execute({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: App.localGet(Int(0), slot3_key),
                TxnField.asset_receiver: Txn.sender(),
                TxnField.asset_amount: Int(1),
                TxnField.fee: Int(0)
            }),
            # clear slot 3
            App.localPut(Int(0), slot3_key, Int(0))
        )),
    )

# validate that the contract is not killswitched
@Subroutine(TealType.none)
def not_killed():
    return custom_assert(App.globalGet(kill_switch_key) == Int(0), err_contract_killed)

# validate caller is the super_admin_address
@Subroutine(TealType.none)
def super_admin_only():
    return fail_if(Gtxn[0].sender() != Addr(super_admin_address), err_unauthorized)

# validate called is admin/creator
@Subroutine(TealType.none)
def admin_only():
    return fail_if(Gtxn[0].sender() != Global.creator_address(), err_unauthorized)

# Handles deleting the contract
# Will send all ALGO And remaining NFTs to the rewards wallet
handle_delete_app = Seq(
    admin_only(),
    InnerTxnBuilder.Execute({
        TxnField.type_enum: TxnType.Payment,
        TxnField.close_remainder_to: Global.creator_address(),
        TxnField.fee: Int(0),
    })
)

# handle user's app close-out and clear-out
# If we have NFTs available in slots and user requests close-out
# we opt into them in the group txn if necessary
# so here we send them
# and then delete local state (likely not needed)
# close-out may fail if not opted in, clear-out will not
handle_close_out = Seq(
    If(Or(
        App.localGet(Int(0), slot1_key) != Int(0),
        App.localGet(Int(0), slot2_key) != Int(0),
        App.localGet(Int(0), slot3_key) != Int(0)
    )).Then(
        sub_collect()
    ),
    App.localDel(Int(0), slot1_key),
    App.localDel(Int(0), slot2_key),
    App.localDel(Int(0), slot3_key),
)

# Main router class
router = Router(
    # Name of the contract
    "draw",
    # What to do for each on-complete type when no arguments are passed (bare call)
    BareCallActions(
        # On create only, just approve
        no_op=OnCompleteAction.create_only(handle_creation),
        # Always let creator update/delete but only by the creator of this contract
        update_application=OnCompleteAction.call_only(super_admin_only), # super admin = 2/2 multisig:nullun+D13
        delete_application=OnCompleteAction.call_only(handle_delete_app), # admin = D13
        opt_in=OnCompleteAction.always(handle_optin), # user opt-in - set up user local state
        close_out=OnCompleteAction.call_only(handle_close_out), # send stored NFTs before closing out state (will fail if user is not opted in)
        clear_state=OnCompleteAction.call_only(handle_close_out) # attempt to send NFTs before clearing state (won't fail)
    ),
)

# admin method to close out all held NFTs to creator address
# intentionally allowed when contract is killed
@router.method
def closeout_nft():
    i = ScratchVar(TealType.uint64)
    return Seq(
        # creator is calling us or fail
        admin_only(),
        # iterate all foreign assets in Txn, close out
        For(i.store(Int(0)), i.load() < Txn.assets.length(),  i.store(i.load() + Int(1))).Do(Seq(
            # close out remaining NFT balances to creator address
            InnerTxnBuilder.Execute({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: Txn.assets[i.load()],
                TxnField.asset_close_to: Global.creator_address(),
            })
        ))
    )

# admin method to opt contract in to NFTs
# opts in to all txn's foreign assets
@router.method
def optin():
    i = ScratchVar(TealType.uint64)
    return Seq(
        # creator is calling us or fail
        admin_only(),
        # disabled when contract is killed
        not_killed(),
        # iterate all foreign assets in Txn, opt in
        For(i.store(Int(0)), i.load() < Txn.assets.length(),  i.store(i.load() + Int(1))).Do(
            # send opt-in to NFT txn
            InnerTxnBuilder.Execute({
                TxnField.type_enum: TxnType.AssetTransfer,
                TxnField.xfer_asset: Txn.assets[i.load()],
                TxnField.asset_receiver: Global.current_application_address(),
            })
        )
    )

# admin method to change global state (8x)
@router.method
def update_state_int(key1: abi.DynamicBytes, val1: abi.Uint64,
                     key2: abi.DynamicBytes, val2: abi.Uint64,
                     key3: abi.DynamicBytes, val3: abi.Uint64,
                     key4: abi.DynamicBytes, val4: abi.Uint64,
                     key5: abi.DynamicBytes, val5: abi.Uint64,
                     key6: abi.DynamicBytes, val6: abi.Uint64,
                     key7: abi.DynamicBytes, val7: abi.Uint64,
                     key8: abi.DynamicBytes, val8: abi.Uint64):
    return Seq(
        # only admin/creator can call update global state
        admin_only(),
        # disabled when contract is killed
        not_killed(),
        If(key1.get() != bytes_empty).Then(App.globalPut(key1.get(), val1.get())),
        If(key2.get() != bytes_empty).Then(App.globalPut(key2.get(), val2.get())),
        If(key3.get() != bytes_empty).Then(App.globalPut(key3.get(), val3.get())),
        If(key4.get() != bytes_empty).Then(App.globalPut(key4.get(), val4.get())),
        If(key5.get() != bytes_empty).Then(App.globalPut(key5.get(), val5.get())),
        If(key6.get() != bytes_empty).Then(App.globalPut(key6.get(), val6.get())),
        If(key7.get() != bytes_empty).Then(App.globalPut(key7.get(), val7.get())),
        If(key8.get() != bytes_empty).Then(App.globalPut(key8.get(), val8.get())),
    )

# mint "free draw" nft that is accepted in lieu of ticket by free_draw entry point
# admin only; admin must pay the ticket price into the rewards pool
@router.method
def get_free_draw_nft(num: abi.Uint64):
    return Seq(
        # only creator can make free_draw nfts
        admin_only(),
        # disabled when contract is killed
        not_killed(),
        # drawing enabled check. is this pointless?
        fail_if(App.globalGet(ticket_key) == Int(0), err_drawing_disabled),
        # first group txn type == algo payment
        fail_if(Gtxn[0].type_enum() != TxnType.Payment, err_payment_incorrect),
        # multisig rewards should receive the funds
        fail_if(Gtxn[0].receiver() != Addr(rewards_pool_address), err_payment_incorrect),
        # validate enough paid
        fail_if(Gtxn[0].amount() != num.get() * App.globalGet(ticket_key), err_payment_amount_invalid),
        # send NFT 
        InnerTxnBuilder.Execute({
            TxnField.type_enum: TxnType.AssetTransfer,
            TxnField.xfer_asset: App.globalGet(free_draw_nft_id_key),
            # hardcoded to creator address
            TxnField.asset_receiver: Global.creator_address(),
            TxnField.asset_amount: num.get(),
            # user pays fees
            TxnField.fee: Int(0)
        })
    )

# function to validate ALGO payments for 1/3 draws or 1/2/3 burns
# fails is drawing is disabled (ticket price == 0)
# ticket_key will be 'ticket' or 'burn_ticket'
@Subroutine(TealType.none)
def validate_payment(multiplier, ticket_key):
    return Seq(
        # multiplier is 1 or 3
        # validate first payment: (algo) payment type
        fail_if(Gtxn[0].type_enum() != TxnType.Payment, err_payment_incorrect),
        # validate first payment: multiplier times ticket price
        fail_if(Gtxn[0].amount() != multiplier * App.globalGet(ticket_key), err_payment_amount_invalid),
        # receiver must be rewards pool address
        fail_if(Gtxn[0].receiver() != Addr(rewards_pool_address), err_payment_incorrect),
    )

# function to validate Free Draw NFT payments for 1x Free Draw
# fails is drawing is disabled (ticket price == 0)
@Subroutine(TealType.none)
def validate_free_draw_payment(multiplier):
    return Seq(
        # drawing enabled check. redundant as we check in queue_draw but we deployed it like this, so :)
        fail_if(App.globalGet(ticket_key) == Int(0), err_drawing_disabled),
        # validate free draw payment with free draw nft
        fail_if(Gtxn[0].type_enum() != TxnType.AssetTransfer, err_payment_incorrect),
        # multiplier will be = 1
        fail_if(Gtxn[0].asset_amount() != multiplier, err_payment_amount_invalid),
        # free draw NFTs should be sent to the application address
        fail_if(Gtxn[0].asset_receiver() != Global.current_application_address(), err_payment_incorrect),
        # confirm NFT ID matches
        fail_if(Gtxn[0].xfer_asset() != App.globalGet(free_draw_nft_id_key), err_payment_incorrect)
    )

# get random bytes from randomness oracle for $acct, round $rand_count - $cur
# going before $rand_count is safe because rounds [$seed-7 -> $seed] are seeded by $seed round
# returns random 32 byte / 256 bit sequence
# account here is BYTES, not index like in other calls
@Subroutine(TealType.bytes)
def get_random_bytes(acct, rand_round, cur):
    res = abi.DynamicBytes()
    return Seq(
        # call the randomness contract
        # passing round-$i (i in 0,1,2) and user address as user_bytes
        # for multiple calls we prefer to call 3x for transparency w/ end users
        InnerTxnBuilder.ExecuteMethodCall(
            app_id=App.globalGet(oracle_app_id_key),
            method_signature="get(uint64,byte[])byte[]", # using get instead of must_get intentionally, handling zero byte return further down when randomness isn't ready (pretty error message)
            args=[Itob(Minus(rand_round, cur)), acct],
            extra_fields=zero_fee_extra_fields, # user pays fees
        ),
        # last_log is the encoded ABI return value
        # It must begin with hex 151f7c75
        fail_if(Len(InnerTxn.last_log()) < Int(6), err_oracle_invalid),
        fail_if(Substring(InnerTxn.last_log(), Int(0), Int(4)) != bytes_151f7c75, err_randomness_fail),
        # decode the ABI return value into res scratch slot
        abi.DynamicBytes.decode(res, Substring(InnerTxn.last_log(), Int(4), Len(InnerTxn.last_log()))),
        # check that it isn't zero (eg when randomness was not ready yet)
        fail_if(res.get() == bytes_empty, err_randomness_fail),
        Log(bytes_rand),
        Log(res.get()),
        Return(res.get())
    )

# helper to get int value from storage contract's global storage
@Subroutine(TealType.uint64)
def get_ext_storage(keynum):
    extvalue = App.globalGetEx(storage_app_id_int, Itob(keynum))
    return Seq(
        extvalue, # must include this or pyteal compilation fails
        Return(extvalue.value())
    )

# pick next round as a draw target
# if we are at round mod 8 == 0 it is safe to use current round
# as the randomness seed is based on this current block's signature
# so it can't be known yet
# otherwise choose the next multiple of 8
@Subroutine(TealType.uint64)
def get_next_rand_round():
    return Return(Cond( # I don't know why this isn't an If() statement. Don't ask. Still, effectively the same.
        [Mod(Global.round(), Int(8)) == Int(0), Global.round()],
        [Int(1), Add(Global.round(), Minus(Int(8), Mod(Global.round(), Int(8))))],
    ))

# map a 256 bit random value into one of the NFTs according to their rarity
# team NFT IDs and odds are stored like so:
# 1: TEAM_1_NFT_ID
# 2: TEAM_1_ODDS
# 3: TEAM_2_NFT_ID
# 4: TEAM_2_ODDS + TEAM_1_ODDS
# ...
# 63: TEAM_32_NFT_ID
# 64: SUM(TEAM_ODDS) ~ aka max_odds **MUST BE POWER OF 2 for mapping from 256 bits to be uniform**
# We get a 256 bit random value and do modulo SUM(TEAM_ODDS) (stored in global storage max_odds_key)
# this goes into rand_val and is in [0, max_odds)
# then iterate for(i=2; i<=64; i+=2)
# first nft odds value which is larger than $rand_val is the NFT to return
# the ID is one index before the currently iterated idx
@Subroutine(TealType.uint64)
def get_random_nft_id(acct, rnd, iter):
    i = ScratchVar(TealType.uint64)
    rand_val = ScratchVar(TealType.uint64)
    return Seq(
        # random(256bit) modulo (max_odds) -> to_integer() -> $rand_val
        rand_val.store(Btoi(
            BytesMod( # BytesMod is expensive - could have sliced a few bytes off the rnd tail, Btoi and do int mod so save op costs
                get_random_bytes(acct, rnd, iter), # 256 bit
                Itob(App.globalGet(max_odds_key)) # Must be power of 2
            )
        )),
        Log(bytes_rand_mapped), # debug/log label & mapped rand value
        Log(Itob(rand_val.load())),
        # for all possible NFT values
        For(i.store(Int(2)),
            Le(i.load(), Int(64)),
            i.store(Add(i.load(), Int(2)))
        ).Do(Seq(
            # described in function header doc
            If (get_ext_storage(i.load()) > rand_val.load()).Then(Seq(
                # switch to using i as results storage
                # ID to return is one before the odds that just won
                i.store(get_ext_storage(Minus(i.load(), Int(1)))),
                # assert that the value is not zero
                fail_if(i.load() == Int(0), err_drawing_failed), # Needed?
                Return(i.load())
            ))
        )),
        # failsafe
        fail_if(Int(1), err_drawing_failed),
        # never reached, but pyteal like this
        Return(Int(1))
    )

# translate int(1,2,3) -> slot1_key slot2_key slot3_key or fail the program  
# used by burn entry points to map slot idx to slot name
@Subroutine(TealType.bytes)
def slot_int_to_key(slot_int):
    return Cond(
        [slot_int == Int(1), slot1_key],
        [slot_int == Int(2), slot2_key],
        [slot_int == Int(3), slot3_key],
        [Int(1), Seq(fail(err_invalid_slot), bytes_empty)] # The seq, bytes dance is to satisfy pyteal
    )

# utility to get the first available free NFT slot in user storage
# empty bytes is all slots filled
@Subroutine(TealType.bytes)
def get_free_slot_for(addrIdx):
    return Cond(
        [App.localGet(addrIdx, slot1_key) == Int(0), slot1_key],
        [App.localGet(addrIdx, slot2_key) == Int(0), slot2_key],
        [App.localGet(addrIdx, slot3_key) == Int(0), slot3_key],
        [Int(1), bytes_empty]
    )

# validate (num) 1 or 3 free slots are available in user storage
# used before a draw is queued
@Subroutine(TealType.none)
def validate_free_slots(num, acctIdx):
    return Cond(
        # validating 1 slot available: is there is any free slot we're good
        [num == Int(1), fail_if(get_free_slot_for(acctIdx) == bytes_empty, err_no_free_slot)], # NO FREE SLOT
        # validating all slots are available
        [Int(1), custom_assert(And(
            App.localGet(acctIdx, slot1_key) == Int(0),
            App.localGet(acctIdx, slot2_key) == Int(0),
            App.localGet(acctIdx, slot3_key) == Int(0)
        ), err_slot_not_empty)] # "MUST_COLLECT"
    )

# internal method to queue a draw action into user local storage
@Subroutine(TealType.uint64)
def queue_draw(num, paid_num, ticket_key):
    return Seq(
        # drawing is enabled
        fail_if(App.globalGet(ticket_key) == Int(0), err_drawing_disabled),
        # already a draw in queue? fail
        fail_if(user_draw_amount(Int(0)) != Int(0), err_draw_queued),
        # save amount of draws in key
        App.localPut(Int(0), draw_amount_key, num),
        # save amount paid for refund in case of randomness expiry
        App.localPut(Int(0), draw_amount_paid_key, App.globalGet(ticket_key) * paid_num),
        # get next safe randomness round and save it in key
        App.localPut(Int(0), draw_round_key, get_next_rand_round()),
        # return the round so the user knows how long to wait (+2 in practice for the VRF oracle to be seeded by off-chain service)
        Return(App.localGet(Gtxn[0].sender(), draw_round_key))
    );


# DRAW/BURN_DRAW/FREE_DRAW methods
# these all return the randomness round commitment

# entry point to queue a 1x draw paying with a free draw NFT
@router.method
def free_draw(*, output: abi.Uint64):
    return Seq(
        # disabled when contract is killed
        not_killed(),
        # validate 1x Free Draw NFTs sent or fail
        validate_free_draw_payment(Int(1)),
        # validate there are 1x free NFT slots in user storage or fail
        validate_free_slots(Int(1), Int(0)),
        # add a 1x draw "queue" to user storage and return the round
        # paid == 0 - no refunds for free drawers, sorry
        output.set(queue_draw(Int(1), Int(0), ticket_key))
    )

# entry point to queue a 1x draw paying with ALGO
@router.method
def draw(*, output: abi.Uint64):
    return Seq(
        # disabled when contract is killed
        not_killed(),
        # validate 1x ALGO payments are sent or fail
        validate_payment(Int(1), ticket_key),
        # validate there is 1x free slot available to draw into, or fail
        validate_free_slots(Int(1), Int(0)),
        # add a 1x draw "queue" to user storage and return the round
        output.set(queue_draw(Int(1), Int(1), ticket_key))
    )

# entry point to queue a 3x draw paying with ALGO
@router.method
def draw3(*, output: abi.Uint64):
    return Seq(
        # disabled when contract is killed
        not_killed(),
        # validate 3x ALGO ticket price is sent or fail
        validate_payment(Int(3), ticket_key),
        # validate there are 3x free NFT slots in user storage or fail
        validate_free_slots(Int(3), Gtxn[0].sender()),
        # add a 3x draw "queue" to user storage and return the round
        output.set(queue_draw(Int(3), Int(3), ticket_key))
    )

@router.method
def burn_draw(slot: abi.Uint64, *, output: abi.Uint64):
    return Seq(
        # disabled when contract is killed
        not_killed(),
        # validate passed slot is burnable
        fail_if(App.localGet(Int(0), slot_int_to_key(slot.get())) == Int(0), err_no_burn_available),
        # "burn" NFT - zero out slot$n
        App.localPut(Int(0), slot_int_to_key(slot.get()), Int(0)),
        # validate burn payment sent
        validate_payment(Int(1), burn_ticket_key),
        # skip validate free slot, we just created one
        # add a 1x draw "queue" to user storage and return the round
        output.set(queue_draw(Int(1), Int(1), burn_ticket_key))
    )

@router.method
def burn_draw2(slot1: abi.Uint64, slot2: abi.Uint64, *, output: abi.Uint64):
    return Seq(
        # disabled when contract is killed
        not_killed(),
        # validate passed slots are burnable
        fail_if(slot1.get() == slot2.get(), err_no_burn_hacking), # if sneaky user tries to burn the same slot twice, amuse them with an error message
        fail_if(App.localGet(Int(0), slot_int_to_key(slot1.get())) == Int(0), err_no_burn_available),
        fail_if(App.localGet(Int(0), slot_int_to_key(slot2.get())) == Int(0), err_no_burn_available),
        # "burn" NFTs - zero out slots
        App.localPut(Int(0), slot_int_to_key(slot1.get()), Int(0)),
        App.localPut(Int(0), slot_int_to_key(slot2.get()), Int(0)),
        # validate burn payment sent
        validate_payment(Int(2), burn_ticket_key),
        # skip validate free slot, we just created two
        # add a 1x draw "queue" to user storage and return the round
        output.set(queue_draw(Int(2), Int(2), burn_ticket_key))
    )

@router.method
def burn_draw3(*, output: abi.Uint64):
    return Seq(
        # disabled when contract is killed
        not_killed(),
        # validate 3 NFTs are in user storage to burn
        custom_assert(get_free_slot_for(Int(0)) == bytes_empty, err_no_burn_available),
        # validate 3x ALGO ticket price is sent or fail
        validate_payment(Int(3), burn_ticket_key),
        App.localPut(Int(0), slot_int_to_key(Int(1)), Int(0)),
        App.localPut(Int(0), slot_int_to_key(Int(2)), Int(0)),
        App.localPut(Int(0), slot_int_to_key(Int(3)), Int(0)),
        # add a 3x draw "queue" to user storage and return the round
        output.set(queue_draw(Int(3), Int(3), burn_ticket_key))
    )

# does the actual drawing
# meant to be called by (our) backend to make the UX nicer but isn't restricted as such
# we have exposed a fallback to end users in the frontend if our redundant backends fail
# note we are using "1" as the account index - meaning first foreign account
@router.method
def exec_draw():
    i = ScratchVar(TealType.uint64) # draw number iterator
    return Seq(
        # disabled when contract is killed
        not_killed(),
        # Otherwise do store(txn.accounts.last())
        # if no draw queued, fail
        fail_if(Eq(user_draw_amount(Int(1)), Int(0)), err_no_draw_queued),
        # if round is not past yet, fail
        fail_if(Lt(Global.round(), App.localGet(Int(1), draw_round_key)), err_wait_for_randomness),
        fail_if(randomness_expired(Int(1)), err_randomness_expired),
        # auto-inner TXN to storage app to increase budget - triggers when 3x draw
        opup.ensure_budget(Int(450) * user_draw_amount(Int(1))),
        # for i=0; i<user.draw_amount; i++
        For(i.store(Int(0)), Lt(i.load(), user_draw_amount(Int(1))), i.store(Add(i.load(), Int(1)))).Do(Seq(
            App.localPut(
                Int(1),
                # get free NFT slot for user
                get_free_slot_for(Int(1)),
                # save a random NFT ID
                get_random_nft_id(
                    Txn.accounts[Int(1)],
                    App.localGet(Int(1), draw_round_key), # agreed upon round
                    i.load() # draw number in [0, 1, 2]
                 )
            )
        )),
        # reset queued draw user storage keys
        reset_user_draw_state(Int(1))
    )

# collect all available NFTs from user storage slots
# intentionally left enabled when contract is killed
@router.method
def collect():
    return sub_collect()

# refund in case randomness has expired
# this should never happen, but has to be factored in anyhow
# free draw users are SOL
# paying users get their money back & a digital apology
# intentionally left available when contract is killed
@router.method
def refund(*, output: abi.Uint64):
    amount = ScratchVar(TealType.uint64) # draw amount to refund
    addr = ScratchVar(TealType.bytes) # address to refund
    return Seq(
        # Otherwise do store(txn.accounts.last())
        # if no draw queued, fail
        addr.store(Txn.accounts[Int(1)]),
        fail_if(user_draw_amount(Int(1)) == Int(0), err_no_draw_queued),
        # if round is not past yet, fail
        custom_assert(randomness_expired(Int(1)), err_randomness_not_expired),
        amount.store(App.localGet(Int(1), draw_amount_paid_key)),
        # for i=0; i<user.draw_amount; i++
        # reset queued draw user storage keys
        reset_user_draw_state(Int(1)),
        InnerTxnBuilder.Execute({
            TxnField.type_enum: TxnType.Payment,
            TxnField.receiver: Txn.sender(),
            TxnField.amount: amount.load(),
            TxnField.fee: Int(0),
        }),
        output.set(amount.load()),
    )

def get_contracts():
    return router.compile_program(version=6)
