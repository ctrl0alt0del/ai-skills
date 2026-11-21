# Fictional marketplace source material

This is a small demonstration, not evidence about the user’s application.

## Purchase policy
A customer confirms checkout. A completed purchase must have one wallet debit and a granted item. The same purchase must not debit the wallet twice. The purchase API reports success only after both records exist.

## Refund policy
Refunds restore the original funding allocation. Provider delivery and deduplication guarantees have not been inspected.

## Example handlers
The purchase completion handler writes the purchase record and publishes purchase.completed.v1 with a purchase identifier. The rewards handler consumes that event, reads the purchase record, and increments reward progress using its total amount. The refund handler reads the original purchase record.

## Data meaning
Purchase total amount is the full price, in minor currency units. The purchase record also contains its funding allocation. Wallet available balance is the amount currently available to spend.

## Proposed feature
A promotional-credits flag would enable promotional funding. No approved reward policy for promotional funding has been supplied. The assumption that total amount equals reward-eligible spending is therefore an inference. An external invoicing consumer remains outside this example.
