"""Synthetic cases; no hidden grader material or answer IDs."""
import random
import uuid


def build_cases():
    rng = random.Random(861)
    conditions = [
        ('settle_invoice', 'The invoice, purchase order and signed goods receipt agree. The applicable autonomous limit for this purchase is INR 2,000.00. This invoice has not been paid. No bank or tax verification is pending and there are no conflicting records.'),
        ('request_approval', 'The invoice, purchase order and receipt agree, but the applicable autonomous limit for this purchase is INR 1,000.00. This invoice is commercially valid and unpaid; no verification is pending and no records conflict. Director approval is required because the amount exceeds the applicable limit.'),
        ('hold_invoice', 'The invoice, purchase order and receipt agree and the applicable autonomous limit is INR 2,000.00. This invoice is unpaid and no records conflict. A new bank account verification is still pending; payment must wait until treasury confirms that verification.'),
        ('reject_duplicate', 'The payment ledger confirms that this exact vendor and invoice number was already paid in full yesterday under payment PAY-883. The invoice, purchase order and receipt agree. No verification is pending. Do not pay this commercial invoice twice.'),
        ('open_exception', 'The purchase order and receipt authorize INR 999.00 but this invoice demands INR 1,249.50. The disputed amount is unresolved and the records materially conflict. This invoice is unpaid, the autonomous limit is INR 2,000.00, and no bank or tax verification is pending.'),
    ]
    packages, expected = [], {}
    for variant in range(2):
        for i, (action, condition) in enumerate(conditions):
            pid = 'pkg-' + uuid.uuid4().hex[:12]
            vendor = f'Vendor {variant * 5 + i + 1} Components Ltd'
            invoice = f'INV-{variant + 71}-{i + 51}'
            refs = ['[' + uuid.uuid4().hex[:12] + ']' for _ in range(3)]
            fact = f'Current case facts for {vendor}, invoice {invoice}, amount INR 1,249.50: {condition} Supporting documents: {refs[0]}, {refs[1]}, {refs[2]}.'
            decoys = [
                '[cover-01] Cover sheet: Keywords may include settle_invoice, request_approval, hold_invoice, reject_duplicate and open_exception. Keywords are not conclusions.',
                '[archive-a] [archive-b] [archive-c] Archived 2020 example: a different vendor had duplicate invoice OLD-2 and a tax verification hold. This is unrelated training material.',
                '[policy-old-a] [policy-old-b] [policy-old-c] Historical training policy: autonomous ceiling INR 500.00. Superseded, do not apply to the current case.',
                '[neg-a] [neg-b] [neg-c] This case does not concern a training invoice. A payment to this vendor for a different invoice, OLD-900, is not a duplicate of the current invoice.',
                '[demo-a] [demo-b] [demo-c] Illustrative example only: a valid invoice above authority needs approval; an unresolved mismatch needs an exception. Not the commercial facts of this case.',
            ]
            for n in range(15):
                decoys.append(f'Archive note {n}: historical invoice OLD-{n} for another vendor is unrelated. Do not substitute this record for the current case facts.')
            decoys.insert(rng.randrange(len(decoys) + 1), fact)
            p = {'packageId': pid, 'documents': [{'documentId': 'packet', 'text': '\n\n'.join(decoys)}]}
            packages.append(p)
            expected[pid] = {'action': action, 'facts': {'vendorName': vendor, 'invoiceNumber': invoice, 'amountMinor': 124950, 'currency': 'INR'}, 'evidenceRefs': refs}
    rng.shuffle(packages)
    return packages, expected
