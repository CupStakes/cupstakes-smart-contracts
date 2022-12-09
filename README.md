# CupStakes Drawing Contract

Deployed as [951618646](https://algoscan.app/app/951618646) (storage: [951618464](https://algoscan.app/app/951618464)) on Algorand MainNet.

Frontend: [CupStakes.World](https://cupstakes.world)

## Overview

CupStakes is a World Cup 2022 Lottery Sweepstakes backed by a VRF beacon.

The drawing contract under `draw/sc.py` implements the drawing functionality.

It employs a "storage contract" - `storage/sc.py` - for extra global storage in order to maintain the Team NFT IDs and Team Odds in a readable manner. It is certainly possible to have fit everything in one contract, but transparency and auditability was a key objective, and the cost of an extra contract was negligible.

## Methods &  Mechanics

See also the [ABI contract JSON file](/draw/contract.json)

### Public Play methods

#### draw1/draw3

Pay for 1 or 3 draws. Commit to a draw execution in the near future.

#### exec_draw

Actual draw execution. In CupStakes this isn't usually/necessarily executed by end users, for usability reasons.

#### collect

Collect Team NFTs in user wallet

#### burn/burn2/burn3

Like draw/draw3 but "burning" an **uncollected** drawn Cupstake for a 22% discount on ticket price

#### free_draw

Like draw (x1) but accepts a Free Draw ASA in lieu of payment. Used for social promotions.

#### refund

failsafe method to refund payment & reset user state in case their draw wasn't executed within the liveness of the committed round. Should never happen. 

### Admin methods

Only accessible to creator account

#### get_free_draw_nft

Returns a specified quantity of the Free Draw ASA to be used in lieu of payments. Full ticket payment is expected.

#### optin

Opt contract into ASAs provided as foreign_assets in the appliation call

#### update_state_int

Update up to 8x [key, uint64 value] pairs in global storage

#### closeout_nft

Method to close out remaining NFT assets to the creator account. To be used at the end of the Draw period. NFTs will then be provably burned by rekeying their holder account to the zero address.

## Storage

We use user-side local storage as well as the global storage of two contracts. 

Team NFT and Odds are stored in the External `storage/sc.py` [contract](storage/sc.py).

### Draw Contract: Local / User

| Type   | Key              | Description                       | Example Value   |
| ----   | ---------------- | --------------------------------- | -------------   |
| Uint64 | draw_round       | Round commitment for randomness   | 24304245        |
| Uint64 | draw_amount      | Amount of CupStakes NFTs to draw  | 3               |
| Uint64 | draw_amount_paid | mALGO paid (mostly for refunds)   | 6600000         |
| Uint64 | slot1            | ID of drawn NFT (1)               | 951510510 (BRA) |
| Uint64 | slot2            | ID of drawn NFT (2)               | 0               |
| Uint64 | slot3            | ID of drawn NFT (3)               | 0               |

### Draw Contract: Global

| Type   | Key                        | Description                                   | Example Value   |
| ----   | -------------------------- | ---------------------------------             | -------------   |
| Uint64 | kill                       | Kill Switch. Disables most functionality (1)  | 0               |
| Uint64 | free_draw_nft              | NFT ID for Free Draw token                    | 951510962       |
| Uint64 | ticket                     | Ticket price in microALGO                     | 2200000         |
| Uint64 | burn_ticket                | Burn & Re-draw price in microALGO             | 1716000         |
| Uint64 | oracle_app_id              | Randomness beacon App ID                      | 947957720       |
| Uint64 | max_odds                   | Max Odds used in Storage Contract             | 1048576         |
| Uint64 | max_randomness_range       | Randomness "timeout", used for refunds        | 1000            |

### Storage Contract: Global

The Storage contract is used to store a series of [NFT ID, Cumulative Odds] entries.

| Type   | Key                        | Description                                   | Example Value   |
| ----   | -------------------------- | ---------------------------------             | -------------   |
| Uint64 | \x01 - \x63 (mod 2)        | NFT IDs                                       | 951510510       |
| Uint64 | \x02 - \x64 (mod 2)        | Cumulative Odds                               | 2200            |

## VRF Drawing

### Commitment in advance

It is important to commit to a round for randomness before it is known.

The only secure way we found to do this is to take custody of payment. See [Non-Viable Approaches](#non-viable-approaches) for some ideas we rejected.

We have followed the recommendations by Algorand inc **with the exception of committing to the currently executed round**: if a draw/burn/... request comes in at a multiple-of-8 round, which are the rounds of "new" randomness, we commit to that round instead of that+8. For a fully secure implementation, 

Due to this design decision, CupStakes is technically open to an attack by collusion between the block proposer and the VRF private key holder. The block proposer can't choose the Block Header which is utilized to produce the VRF seed, but if they had access to the VRF service private key, they would be able to "peek" at the result and only execute a draw payment if the result would be favorable.

To make this more secure:

- while retaining speed/usability: remove `[Mod(Global.round(), Int(8)) == Int(0), Global.round()]` logic in [/draw/sc.py#L421](get_next_rand_round)

- if immediate results are not required: commit to a round far in advance

See also:

[Randomness on Algorand](https://developer.algorand.org/articles/randomness-on-algorand/?from_query=randomness)

[Usage and Best Practices for Randomness Beacon](https://developer.algorand.org/articles/usage-and-best-practices-for-randomness-beacon/?from_query=randomness)

### Mapping randomness to an NFT selection

A reasonably large power of two is chosen as the maximum odds of an NFT. In our case 1048576 (2^20) was deemed large enough.

A virtual table is "created" out of the storage contract's global storage that covers the [0, max_odds] space

Example:

```
# 1: TEAM_1_NFT_ID - e.g. 951510510 Brazil
# 2: TEAM_1_ODDS - e.g. 2200 
# 3: TEAM_2_NFT_ID - e.g. 951510511 Argentina
# 4: TEAM_2_ODDS + TEAM_1_ODDS - e.g. 4900
# .. 
# 63: TEAM_32_NFT_ID - e.g. 951510511 Argentina
# 64: TEAM_32_ODDS + TEAM_31_ODDS + ... - e.g. 1048576
```

Assuming maximum odds of 1048576, in the above example:

Brazil's odds would be (2200 - 1 - 0) / 1048576 = 0.20971%

Argentina's would be (4900 - 1 - 2200) / 1048576 = 0.25739%

The logic is implemented in [get_random_nft_id](draw/sc.py#L440).

The VRF beacon produces a value of 256 bits. The 256 bit VRF output is mapped to the space [0, max_odds - 1] via modulo operation ("small rand value")

**If you are implementing your own lottery or otherwise using VRF output, pay close attention to your modulus during this operation**. Using anything but a power of 2 would produce a skewed distribution because more values would be mapped to the beginning of the space than the end. As an example, if you mapped [0..7] to [0..5] via mod 6, you would get twice as many 0s (from inputs 0 and 6) and 1s (from inputs 1 and 7) than 2, 3, 4 and 5.

The storage space is then iterated from 2..64 mod 2, and we stop at the first team/stored value that is _larger_ than the "small rand val". We return the NFT ID found in the previous global storage slot.

To continue with the above example values for Brazil/Argentina:

- assume the VRF oracle produced a value that has uint representation of 13636055 after Btoi()

- 13636055 % 1048576 = 4567 ("small rand value")

- we would start at i=2, inspect Brazil's odds at global storage slot x02, and see that 2200 > 4567 is false,

- continue at i=4, inspect Argentina's odds at slot x04 and see that 4900 > 4567 is true

- we would return the NFT ID for Argentina, stored at global storage slot x03

- this would be stored at slotN for the user to collect or burn

## Code Updatability

The Draw Smart Contract is updatable by a 2/2 multisig between D13 and Nullun.

## Storage/Params Updatability

The Global Storage params are updatable via the `update_state_int` method by the creator account (controlled by D13).

## Deletability

The Smart Contract is deletable by the creator account/D13.

## Rewards Multisig

Rewards are not stored on the contract, but instead sent to a 5/8 Multisig between the CupStakes [Trust Partners](#trust-partners).

## Addresses

| Type                      | Address                    | Owner | Description                                   |
| ------------------------- | -------------------------- | --------------------------------- | --- |
| Creator/Admin             | CUPSTAKEOXXAYC3AOAK7QG3A6776Z4TKLOGFYN6CKMR33BT6QLFBEPJY4U | D13 | Contracts & NFT creator account |
| SuperAdmin                | 7H7KSVOVKI6CCQWDOH4RWP4RHSRBMQBAVW25PUMDAOZVJVSDBDLWF47ORQ | 2/2 msig D13/nullun | Draw SC code update address  |
| Rewards Pool              | BSJAMHBCLLSOBW4GAP2DWACN7B3VPEPSU6SCUTVBS3HRC7UZ35Z2KLVLF4 | 5/8 msig w/ Trust Partners | ALGO Rewards Pool |
| Backend Executor 1        | CUPSTAKEDBJH22LDXFP24GJP4Y2MB4IT7E7CFDFCEMJVJXHA3VCWNVOFVA | D13 | Calls draw_exec for users |
| Backend Executor 2        | CUPSTAKEXSBZPZP4H2SUELHPJ22RIUA55PY2IUUXWQNTZ237R6P2E2UXJE | D13 | Calls draw_exec for users |
| Free Draw Distributor     | CUPS7VGF4UEBEQZL6QLWXLWHAKKXELAK5D5HRYOBJMXICKIZZUWG54TAKE | D13 | Distribute Free Draws (CUPS..TAKE) |

## Trust Partners

| Name             |        Address                                              | Bio                                                          |  Twitter Handle    |
| ---------------- | ----------------------------------------------------------- | ------------------------------------------------------------ |  ----------------- |
| D13.co//ective	 | DTHIRTEENNLSYGLSEXTXC6X4SVDWMFRCPAOAUCXWIXJRCVBWIIGLYARNQE  | CupStakes Developers & Operators	                            |  @D13_co           |
| Fred Estante [1] | FREDESTDMDNTXKITMO2TEL4ADKHJ7KOQ4HHCX7N65NGTBNLUCWI7W7AMHY  | Head of Product Marketing, Algorand Foundation	              |  @FredEstante      |
| Nullun [1]	     | XWJ6UN54FCW2P56QFBROUYXNYOU3RXKO6RN7ICM3FZA4JZJ4ZMUVB66ZBQ  | Developer Relations, Algorand Inc	                          |  @nullun           |
| Choppa [1]	     | WQFL4RWPTTWA5GP2RFBTKYQDW5F3AFXGLVESQUQMAZPPPPCZQS5ECM74BE  | Core community manager, Pact.fi	                            |  @CHOPPAtheSHARK   |
| Zeus.algo	       | ZGW3KIA4KUSRBEFJZQZ2Z5HVHPEWOPMVP4HQ2HVG5LIR57JP6WO4AARWUQ  | Large NFT Aquatic Mammal                                     |  @zeus_algo        |
| LeavemeaGnome	   | 5TOOCJSAZBPBV4LFH26JBJYPJXGQ4XCYAT44Z6ETZT6DHQIEIBTYBYW4CM  | Creator of Orbital Gnome Strikers, Larger NFT Aquatic Mammal	|  @LeavemeaGnome    |
| SaxoSays	       | QPF3SYXFQK7MVTYZGCJ5JQLQLGA3QMYIOPVMQ3SK2O2IQTYRZUYXQ6EW34  | Blockchain Consultantct                                      |  @SaysSaxo         |
| Farzan Akhtar	   | FKIJ4VYTHYGVYXQZ73CDHGHMDP2DQKF2V3EFQFSNX5OEFFUKQO6AFGHADY  | "Blockchain and Beyond", podcast host                        |  @FarzanAkhtar1    |

[1] Participating as individuals; not an endorsement by the Algorand Foundation, Algorand inc or Pact.fi

## Non-Viable Approaches

### Not taking full custody of payment

In order to avoid having to implement a chain-watching backend executor of draws that call exec_draw we considered designing a payment+draw transaction from users in advance and executing it when the randomness is ready.

**This approach is not secure** because the user can control the outcome of the transaction by controlling their balance. If they could inspect the outcome of the draw from the VRF output before we can execute the transaction, they would be able to move enough funds out of their account to make the transaction fail, thus gaming the system.

## Misc

CupStakes has specific characteristics and requirements that may not be applicable to all lotteries:

- "infinite" supply of NFTs

- varied and mutable but static odds

As such the contract:

- just assumes that the NFT chosen will be "in stock". We mint 10 million of each Team and do not expect to have a stock issue.
- Odds must be updatable and will vary wildly - Brazil will start at 0.2% odds, whereas common teams will have significantly higher Draw odds.
- Odds are not dynamically calculated, as they could be in a raffle with limited "stock".

