"""Synthetic sample contracts used by demo mode, the sample generator and the tests.

All parties, amounts and terms are invented. They are *not* legal templates.
"""
from __future__ import annotations

MSA_V1 = """MASTER SERVICES AGREEMENT

This Master Services Agreement (the "Agreement") is entered into as of January 15, 2025 (the "Effective Date") by and between Acme Manufacturing Inc., a Delaware corporation ("Customer"), and Northwind Cloud Systems LLC, a Texas limited liability company ("Vendor").

1. DEFINITIONS AND SERVICES
1.1 Services. Vendor shall provide the cloud hosting, monitoring and support services described in each Statement of Work (the "Services").
1.2 Statements of Work. Each Statement of Work is governed by this Agreement and, in the event of conflict, this Agreement prevails.

2. TERM
2.1 Initial Term. This Agreement commences on the Effective Date and continues for twenty-four (24) months (the "Initial Term"), unless terminated earlier in accordance with Section 8.
2.2 Renewal. Following the Initial Term, this Agreement automatically renews for successive twelve (12) month periods (each a "Renewal Term") unless either party gives the other written notice of non-renewal at least ninety (90) days before the end of the then-current term.

3. FEES AND PAYMENT
3.1 Fees. Customer shall pay Vendor the fees set out in the applicable Statement of Work. The initial annual subscription fee is USD 240,000.
3.2 Invoicing and Payment. Vendor shall invoice Customer monthly in arrears. Customer shall pay each undisputed invoice within thirty (30) days after receipt of the invoice.
3.3 Late Payment. Overdue amounts accrue interest at one percent (1%) per month.
3.4 Fee Adjustments. Vendor may increase fees for a Renewal Term by no more than five percent (5%) by giving Customer written notice at least sixty (60) days before the start of that Renewal Term.

4. SERVICE LEVELS
4.1 Availability. Vendor shall maintain Monthly Uptime of at least 99.9% for the production Services.
4.2 Service Credits. If Monthly Uptime falls below 99.9%, Customer is entitled to a service credit equal to five percent (5%) of the monthly fees for each 0.1% shortfall, up to a maximum of thirty percent (30%) of the monthly fees.
4.3 Reporting. Vendor shall deliver a written service performance report to Customer on or before the fifth (5th) Business Day of each calendar month.

5. DATA PROTECTION AND SECURITY
5.1 Security Incidents. Vendor shall notify Customer in writing within seventy-two (72) hours after becoming aware of a Security Incident affecting Customer Data.
5.2 Return of Data. Within thirty (30) days after expiration or termination of this Agreement, Vendor shall return or delete all Customer Data at Customer's written request.

6. CONFIDENTIALITY
6.1 Obligations. Each party shall hold the other party's Confidential Information in confidence and use it only to perform this Agreement.
6.2 Survival. These confidentiality obligations survive for five (5) years after termination or expiration of this Agreement.

7. INDEMNITY AND LIABILITY
7.1 Indemnity. Vendor shall defend and indemnify Customer against third-party claims alleging that the Services infringe intellectual property rights.
7.2 Limitation of Liability. Except for indemnification and confidentiality obligations, each party's aggregate liability is limited to the fees paid or payable in the twelve (12) months preceding the claim.
7.3 Exclusion of Damages. Neither party is liable for indirect or consequential damages.

8. TERMINATION
8.1 Termination for Convenience. Customer may terminate this Agreement for convenience by giving Vendor sixty (60) days' prior written notice.
8.2 Termination for Cause. Either party may terminate this Agreement if the other party materially breaches it and fails to cure the breach within thirty (30) days after written notice of the breach.

9. INSURANCE AND AUDIT
9.1 Insurance. Vendor shall maintain commercial general liability insurance of at least USD 2,000,000 and shall deliver a certificate of insurance to Customer within ten (10) days after the Effective Date.
9.2 Audit. Customer may audit Vendor's compliance with this Agreement once per year upon fifteen (15) Business Days' prior written notice.

10. GENERAL
10.1 Governing Law. This Agreement is governed by the laws of the State of Delaware, without regard to conflict of laws principles.
10.2 Assignment. Neither party may assign this Agreement without the other party's prior written consent.
10.3 Notices. Notices must be in writing and delivered to the addresses stated in the applicable Statement of Work.

IN WITNESS WHEREOF, the parties have executed this Agreement as of the Effective Date.
Acme Manufacturing Inc.    Northwind Cloud Systems LLC
"""

AMENDMENT_1 = """AMENDMENT NO. 1 TO MASTER SERVICES AGREEMENT

This Amendment No. 1 (the "Amendment") is entered into as of June 1, 2025 (the "Amendment Effective Date") by and between Acme Manufacturing Inc. ("Customer") and Northwind Cloud Systems LLC ("Vendor") and amends the Master Services Agreement dated January 15, 2025 (the "Agreement").

1. PAYMENT TERMS
1.1 Section 3.2 of the Agreement is amended so that Customer shall pay each undisputed invoice within forty-five (45) days after receipt of the invoice.

2. NON-RENEWAL NOTICE
2.1 In Section 2.2 of the Agreement, the words "ninety (90) days" are replaced with "sixty (60) days".

3. DATA RESIDENCY
3.1 A new Section 5.3 is added to the Agreement: Vendor shall store Customer Data only in data centers located in the United States.

4. TERMINATION FOR CONVENIENCE
4.1 Section 8.1 of the Agreement is amended to require ninety (90) days' prior written notice.

5. GENERAL
5.1 Except as expressly amended, the Agreement remains in full force and effect.
"""


def msa_v2_restated() -> str:
    """The MSA with Amendment No. 1 folded in (used as a second *version* for comparison)."""
    text = MSA_V1
    replacements = [
        ("MASTER SERVICES AGREEMENT\n\nThis Master Services Agreement (the", "AMENDED AND RESTATED MASTER SERVICES AGREEMENT\n\nThis Amended and Restated Master Services Agreement (the"),
        ("shall pay each undisputed invoice within thirty (30) days after receipt of the invoice.", "shall pay each undisputed invoice within forty-five (45) days after receipt of the invoice."),
        ("written notice of non-renewal at least ninety (90) days before", "written notice of non-renewal at least sixty (60) days before"),
        ("by giving Vendor sixty (60) days' prior written notice.", "by giving Vendor ninety (90) days' prior written notice."),
        ("6. CONFIDENTIALITY", "5.3 Data Residency. Vendor shall store Customer Data only in data centers located in the United States.\n\n6. CONFIDENTIALITY"),
    ]
    for old, new in replacements:
        if old not in text:
            raise ValueError(f"sample replacement target missing: {old[:40]!r}")
        text = text.replace(old, new, 1)
    return text


SAAS = """SOFTWARE-AS-A-SERVICE SUBSCRIPTION AGREEMENT

This Software-as-a-Service Subscription Agreement (the "Agreement") is made on March 1, 2025 (the "Effective Date") between Helios Analytics Inc., a California corporation ("Provider"), and Contoso Retail Ltd., a company registered in England and Wales ("Customer").

1. SUBSCRIPTION
1.1 Grant. Provider grants Customer a non-exclusive right to access the Helios Insights platform (the "Platform") for internal business purposes during the Subscription Term.
1.2 Onboarding. Provider shall complete onboarding of Customer within ten (10) Business Days after the Effective Date.
1.3 Acceptance. Customer shall deliver written acceptance or rejection of the Implementation Deliverables within five (5) Business Days after delivery of the Implementation Deliverables.

2. TERM AND RENEWAL
2.1 Subscription Term. The initial term is twelve (12) months from the Effective Date.
2.2 Auto-Renewal. The subscription renews automatically for successive periods of twelve (12) months unless Customer gives written notice of non-renewal at least thirty (30) days before the end of the then-current term.

3. FEES
3.1 Subscription Fee. The annual subscription fee is USD 84,000, invoiced annually in advance.
3.2 Payment. Customer shall pay each invoice within fifteen (15) days after the invoice date.
3.3 Price Changes. Provider may change the subscription fee at any time by notice to Customer.

4. SUPPORT AND SERVICE LEVELS
4.1 Availability. Provider will use reasonable endeavours to make the Platform available 99.5% of the time each month.
4.2 Support. Provider shall respond to Priority 1 support requests within one (1) hour of notification.
4.3 Improvements. Provider will address defects promptly and in a timely manner.

5. LIABILITY
5.1 Cap. Provider's total liability is limited to two times (2x) the fees paid in the twelve (12) months before the claim.
5.2 Customer Indemnity. Customer shall indemnify Provider against all losses arising from Customer's use of the Platform, without limit.

6. CONFIDENTIALITY
6.1 Each party shall keep the other party's Confidential Information confidential during the term and for three (3) years afterwards.

7. GENERAL
7.1 Governing Law. This Agreement is governed by the laws of England and Wales.
7.2 Assignment. Provider may assign this Agreement to an affiliate without notice to Customer.
"""

NDA = """MUTUAL NON-DISCLOSURE AGREEMENT

This Mutual Non-Disclosure Agreement (the "Agreement") is effective as of September 1, 2025 (the "Effective Date") between Acme Manufacturing Inc. ("Acme") and Orion Robotics GmbH ("Orion"), each a "Party".

1. PURPOSE
1.1 The Parties wish to evaluate a potential supply relationship and may disclose Confidential Information to each other for that purpose only.

2. CONFIDENTIALITY OBLIGATIONS
2.1 Protection. The receiving Party shall protect Confidential Information using at least the same degree of care it uses for its own confidential information, and no less than reasonable care.
2.2 Permitted Disclosure. The receiving Party may disclose Confidential Information only to employees who need to know it and are bound by written confidentiality obligations.
2.3 Return. Within thirty (30) days after the disclosing Party's written request, the receiving Party shall return or destroy all Confidential Information.

3. TERM
3.1 Disclosure Period. Confidential Information may be disclosed during the two (2) years following the Effective Date.
3.2 Survival. The obligations in Section 2 survive for three (3) years after the end of the disclosure period.

4. GENERAL
4.1 Governing Law. This Agreement is governed by the laws of the State of New York.
4.2 No Assignment. Neither Party may assign this Agreement without the prior written consent of the other Party.
"""

SAMPLES: dict[str, str] = {
    "msa_v1": MSA_V1,
    "msa_amendment_1": AMENDMENT_1,
    "msa_v2_restated": msa_v2_restated(),
    "saas_subscription": SAAS,
    "mutual_nda": NDA,
}
