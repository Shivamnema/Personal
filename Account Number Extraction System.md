---

# Account Number Extraction System
## Presentation to SVP

---

## 1. The Problem We Are Solving

Currently, extracting account numbers from payment messages is a **manual human process.** At the scale of hundreds of thousands of messages per day, this creates three business risks:

- **Speed** — human review cannot keep pace with transaction volume
- **Consistency** — different analysts may interpret ambiguous messages differently
- **Cost** — significant human resource allocation to a repetitive task

The objective of this system is to **automate account number extraction** from payment messages with the highest possible precision, zero hallucinations, and a safe human fallback for edge cases.

---

## 2. Why This Problem Is Hard

Payment messages at bank come in **multiple ISO formats:**

- **ISO 20022** — XML-based structured messages
- **MT / SWIFT** — field-tagged messages with specific codes like `:59:` and `:50K:`
- **ISO 8583** — card transaction binary format
- **Free text / unknown** — narrative fields, corrupted messages, legacy formats

Within any single message, an account number can appear **alongside other number-like strings** such as invoice references, phone numbers, card numbers, sort codes, and transaction IDs. The system must correctly identify which string is the real account number — and must never extract a wrong one, since a misrouted payment is a serious financial incident.

This means our primary constraint is **precision over recall** — it is better to send a message to human review than to auto-route it with a wrong account number.

---

## 3. Why We Cannot Use a Single Tool

Before explaining the architecture, it is important to explain why no single approach solves this problem completely:

**Pure regex / rules** — works perfectly for well-formed structured messages but breaks on free text, unknown formats, and internal account codes that have no fixed pattern.

**Large Language Models (ChatGPT, Claude etc.)** — powerful but fundamentally generative. They can hallucinate plausible-looking account numbers that do not exist in the message. Unacceptable for payment routing.

**ML classifier alone** — cannot mathematically validate whether a string is a real IBAN. Will confuse card numbers, sort codes, and account numbers without additional validation.

**The solution is a layered hybrid architecture** where each layer does what it is best at, and no single layer carries the full responsibility.

---

## 4. The Architecture — Layer by Layer

---

### Layer 1 — Format Detector

Every incoming message first passes through a format detector. This component identifies whether the message is ISO 20022 XML, MT/SWIFT, ISO 8583, or unknown/free-text.

**Why this matters:** Each format stores account numbers in completely different locations. An XML message has account numbers inside specific tags like `<CdtrAcct>`. An MT message has them after field codes like `:59:`. Without knowing the format first, you cannot reliably find where to look.

---

### Layer 2 — Format-Specific Parsers

Once the format is identified, a dedicated parser extracts the relevant fields:

- **ISO 20022 parser** — reads `<CdtrAcct>` (creditor account) and `<DbtrAcct>` (debtor account) XML tags directly
- **MT parser** — reads fields `:50K:`, `:59:`, `:58A:` which correspond to ordering account, beneficiary account, and correspondent account
- **Unknown format** — passes directly to the ML layer

**Why this matters:** For well-formed structured messages, the account number is in a **known, deterministic location.** A parser extracts it with 100% accuracy. No machine learning required. This handles an estimated 60–70% of your total message volume at zero ML cost and zero hallucination risk.

The field name the account number came from — `CdtrAcct` or `:59:` — is also preserved as a feature for downstream scoring. This is important because it tells the system the **role** of the account number, debtor or creditor, which is essential for correct payment routing.

---

### Layer 3 — Candidate Extraction

For messages where the parser cannot cleanly extract an account number — free text, unknown formats, corrupted messages — the system scans the full message text and extracts all candidate strings that could potentially be account numbers.

This uses three pattern types:
- **IBAN patterns** — country code + check digits + BBAN structure
- **Internal account code patterns** — formats like `ACC-00123456`, `IE-98765`
- **Generic numeric patterns** — any long numeric string that could be an account number

A single message may yield multiple candidates. Each candidate is then scored independently.

---

### Layer 4 — Feature Builder

Before scoring, each candidate is enriched with context:

- **The candidate string itself** — the raw extracted value
- **Field of origin** — which XML tag or MT field it came from
- **Surrounding words** — the 5–10 words before and after the candidate in the message
- **Format type** — was this from an ISO 20022 message or free text?

**Why surrounding context matters:** The same string `00123456` means something different when it appears after the word "account" versus after the word "invoice." This context dramatically improves classification accuracy.

---

### Layer 5 — TF-IDF + Logistic Regression Classifier

Each enriched candidate is passed through a machine learning classifier trained on thousands of your own labeled historical payment messages.

**TF-IDF** converts the candidate's character patterns into numeric features. It works by breaking strings into character sub-patterns — for example `IE29AIBK` becomes `IE2`, `E29`, `29A`, `9AI` and so on. Patterns that are distinctive to account numbers receive high scores. Patterns common to phone numbers or invoice references receive low scores.

**Logistic Regression** then makes a binary decision per candidate: is this an account number or not? It outputs a probability score between 0 and 1.

**Why Logistic Regression and not a more complex model:** This is a binary classification problem with well-engineered features. Logistic Regression is fast, interpretable, and performs as well as more complex models for this type of task. It is also easy to explain and audit — important in a regulated banking environment.

---

### Layer 6 — IBAN MOD-97 Checksum Validator

Every candidate that passes the ML classifier is subjected to a **mathematical validation.**

Every IBAN contains two check digits calculated using the MOD-97 algorithm. The validator rearranges the IBAN, converts letters to numbers, and verifies the remainder when divided by 97 equals exactly 1. If it does not, the string is not a valid IBAN regardless of what the ML model scored it.

**Why this is critical:** A card number, phone number, or fabricated string may score well in the ML classifier but will almost always fail MOD-97. This is a hard mathematical filter that sits between the ML output and the routing decision. It is your strongest defence against false positives.

For internal account codes that are not IBANs, format regex and length rules perform equivalent validation.

---

### Layer 7 — Business Rules Engine

After mathematical validation, business rules are applied:

- **Expected length** — IBANs are 15–34 characters, internal codes follow known length patterns
- **Format regex** — structural pattern matching per account type
- **Duplicate detection** — if the same candidate appears multiple times in a message, flag it
- **Role assignment** — based on field of origin, assign whether this is a debtor account (sending) or creditor account (receiving)

**Why role assignment matters:** A payment message contains two account numbers — the account being debited and the account being credited. Routing a payment requires knowing which is which. The field of origin tells us this definitively for structured messages.

---

### Layer 8 — Confidence Scoring and Routing Gate

Every extracted and validated account number receives a final confidence score combining:

- ML classifier probability
- Whether it passed IBAN checksum
- Whether it came from a known structured field (parser path) or ML path
- Business rules outcome

Candidates are then routed based on this score:

**High confidence (≥ 0.85)** → Auto-routed to the payment processing system. No human involvement.

**Low confidence (< 0.85)** → Sent to the human review queue. An analyst confirms or rejects the extraction before any payment action is taken.

**Why 0.85 and not higher:** Starting conservatively means more messages go to human review initially. As the system proves itself in production, the threshold can be raised incrementally. This protects against false positives during the rollout period.

---

### Layer 9 — Human Review Queue and Feedback Loop

Low confidence cases are not dead ends. They are opportunities to improve the system.

Every case a human analyst resolves — confirming or correcting an extraction — is logged and added to the training dataset. The ML model is retrained on a monthly cycle incorporating these new confirmed examples.

**This means the system gets smarter over time on your own production data.** Edge cases that confuse the model today become training examples that improve it next month.

---

### Layer 10 — GLiNER as Targeted Fallback

GLiNER is a state-of-the-art Named Entity Recognition model that is extractive by nature — meaning it can only return strings that literally exist in the input text. It cannot hallucinate.

GLiNER is used **only** for free-text and unknown format messages where the parser has nothing to work with and the TF-IDF features are weak. It is fine-tuned on your labeled historical data specifically for account number extraction.

It is not the primary engine. It is the safety net for the hardest 10–15% of messages.

---

## 5. What Makes This Architecture Safe for Payment Processing

| Risk | How the Architecture Addresses It |
|---|---|
| Wrong account number extracted | MOD-97 hard rejects invalid IBANs mathematically |
| ML hallucination | ML is extractive classifier, not generative — cannot invent strings |
| Unknown message format | Falls to human review, never auto-routes |
| Low confidence result | Threshold gate prevents auto-routing |
| Model degrading over time | Monthly retraining on human-confirmed production data |
| Single point of failure | Three independent layers — parser, ML, validator — must all agree |

---

## 6. Build Sequence — How We Are Developing This

| Phase | What | Status |
|---|---|---|
| Phase 1 | IBAN MOD-97 validator | ✅ Complete |
| Phase 2 | TF-IDF + Logistic Regression classifier | ✅ Complete |
| Phase 3 | Candidate extraction patterns | ✅ Complete |
| Phase 4 | Confidence scoring | ✅ Partially complete |
| Phase 5 | Format detector + ISO 20022 / MT parsers | 🔄 In progress |
| Phase 6 | Human review queue | 🔄 In progress |
| Phase 7 | GLiNER fine-tuning | 📋 Planned |
| Phase 8 | Feedback loop + retraining pipeline | 📋 Planned |

---

## 7. The One Metric That Matters in Production

Once live, the system will be measured on one primary number:

> **Percentage of messages correctly auto-routed with zero human correction**

We start conservatively, auto-routing only the highest confidence results. We raise the threshold incrementally as the system demonstrates accuracy on real production data. This gives full visibility and control to the business at every stage of rollout.

---

## 8. Summary in One Paragraph

We are building a layered intelligent system that reads payment messages in any ISO format, identifies all candidate account numbers, validates them mathematically, scores them using a machine learning model trained on bank's own historical data, and routes high-confidence results automatically while sending edge cases to human review. The human review queue feeds directly back into model retraining, making the system progressively more accurate over time. At no point does the system generate or guess account numbers — every output is a string that existed in the original message and passed mathematical validation. This makes it safe for production payment routing.
