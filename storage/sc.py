from pyteal import *

# dumb contract to be abused for its global storage
# in CupStakes it will hold 32x [NFT ID, NFT CUMULATIVE ODDS] pairs as such:
# 1: TEAM_1_NFT_ID
# 2: TEAM_1_ODDS
# 3: TEAM_2_NFT_ID
# 4: TEAM_2_ODDS + TEAM_1_ODDS
# ...
# 63: TEAM_32_NFT_ID
# 64: SUM(TEAM_ODDS) ~ aka max_odds **MUST BE POWER OF 2 for mapping from 256 bits to be uniform**
# max_odds we will use are 2**20 because why not

bytes_empty = Bytes('')

# validate caller is admin/creator
@Subroutine(TealType.none)
def admin_only():
    return Assert(Txn.sender() == Global.creator_address())

# Main router class
router = Router(
    # Name of the contract
    "storage-contract",
    # What to do for each on-complete type when no arguments are passed (bare call)
    BareCallActions(
        no_op=OnCompleteAction.always(Approve()),
        update_application=OnCompleteAction.never(),
        delete_application=OnCompleteAction.call_only(admin_only),
        # No local state, don't bother handling it.
        close_out=OnCompleteAction.never(),
        opt_in=OnCompleteAction.never(),
        clear_state=OnCompleteAction.never(),
    ),
)

# same update 8x int values as draw contract
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
        Assert(Txn.sender() == Global.creator_address()), # could have used admin_only but OK
        If(key1.get() != bytes_empty).Then(App.globalPut(key1.get(), val1.get())),
        If(key2.get() != bytes_empty).Then(App.globalPut(key2.get(), val2.get())),
        If(key3.get() != bytes_empty).Then(App.globalPut(key3.get(), val3.get())),
        If(key4.get() != bytes_empty).Then(App.globalPut(key4.get(), val4.get())),
        If(key5.get() != bytes_empty).Then(App.globalPut(key5.get(), val5.get())),
        If(key6.get() != bytes_empty).Then(App.globalPut(key6.get(), val6.get())),
        If(key7.get() != bytes_empty).Then(App.globalPut(key7.get(), val7.get())),
        If(key8.get() != bytes_empty).Then(App.globalPut(key8.get(), val8.get())),
    )

def get_contracts():
    return router.compile_program(version=7)
