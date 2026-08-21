# Prompt: Fundamental Analysis of an Indian Listed Company

Use this as the instruction set whenever analyzing a stock. Follow all three stages in order. Stage 2 and Stage 3 are numeric — calculate them precisely from real financial data, don't estimate. Stage 1 is qualitative — research it using real, current sources.

---

## Stage 1 — Qualitative Business Checklist

Answer all 18 questions using real research (company filings, annual reports, news, concall transcripts). For each answer, explicitly note if it represents a red flag.

1. What does the company do?
2. Who are the promoters, and what is their background? Check specifically for: criminal history, SEBI orders/penalties, promoter share pledging, and promoter holding trend over the last 3 years. If control of the company has changed hands, evaluate the current owner separately from any past owner, but state the history clearly either way.
3. What do they manufacture or provide?
4. How many plants/locations, and where?
5. Are plants/facilities running at full capacity?
6. What raw material is required? Flag if it's import-dependent or government-regulated.
7. Who are the clients/end-users? Flag if any single customer is a large share of revenue.
8. Who are the competitors? Is this a monopoly, oligopoly, or fragmented/highly competitive market?
9. Who are the major shareholders, and what are the recent trends in FII/DII/promoter/public holding?
10. Any new products planned? State clearly whether each is a natural extension of the core business or a genuinely new, unrelated business line.
11. Any planned geographic expansion?
12. What's the revenue mix across segments, and which segment is actually driving growth?
13. Is this a heavily regulated environment? State whether regulation acts as a barrier protecting the business, or as a constraint on it.
14. Who are the bankers/auditors? Flag any recent auditor change or resignation and the stated reason.
15. Any headcount or labour issues? Flag strikes, unrest, or heavy dependence on scarce/niche skills.
16. What are the entry barriers for new competitors?
17. Could the product easily be replicated in a cheap-labour country, or does it require real engineering/technical depth to replicate?
18. Does the company have too many subsidiaries? List them with ownership % and purpose; flag anything opaque or unexplained.

**At the end of Stage 1, list every red flag found in one place, separately from the routine answers.**

---

## Stage 2 — Financial Due Diligence (10-Point Checklist)

Pull at least 5 years of financial statements (ideally 10) plus TTM (trailing twelve months) data. Calculate each of the following precisely — don't approximate.

| # | Check | How to calculate | What to look for |
|---|---|---|---|
| 1 | Gross Profit Margin | Gross Profit ÷ Net Sales, where Gross Profit = Net Sales − COGS | Should be above 20%. Note the multi-year trend, not just the latest figure. |
| 2 | Revenue growth vs. Profit growth | Compare compounded growth rates over 3yr, 5yr, 10yr, and TTM windows for both sales and profit | Profit growth should track sales growth. If they diverge sharply, find out why before trusting the number. |
| 3 | EPS consistency | Compare EPS trend to Net Profit trend; separately check how much the share count has grown over the same period | EPS should move in line with profit. Rising share count means dilution — quantify it. |
| 4 | Debt level | Total Borrowings ÷ Total Equity, plus the absolute borrowings trend over time | Should not be highly leveraged, and the trend should not be rising. |
| 5 | Inventory | Inventory Days trend, compared against Operating Margin trend over the same period | Inventory days shouldn't rise faster than margins are improving. |
| 6 | Sales vs. Receivables | Debtor Days trend | Should not be rising faster than sales growth — could indicate weak collections or channel stuffing. |
| 7 | Cash Flow from Operations | CFO for each of the last 5 years | Must be positive every year. Even if positive, flag a declining trend. |
| 8 | Return on Equity | Latest year ROE, plus 3-year and 5-year averages | Should be above 25%. If the latest year is much lower than the multi-year average, explain why (e.g. a recent capital raise, a one-off loss). |
| 9 | Business diversity | Number of distinct business segments, and any recent unrelated acquisitions/JVs | Prefer 1-2 simple, related business lines. Flag if the company is expanding into unrelated areas. |
| 10 | Subsidiaries | List each subsidiary, ownership %, and stated purpose | Too many, or subsidiaries with unclear purpose, can be a vehicle for related-party issues. |

**Important calculation notes — apply these carefully, they matter:**

- **Free Cash Flow = Cash from Operating Activities − Capital Expenditure.** Use the actual itemized CapEx figure where available, not the full net "investing activities" cash flow line — that line often includes non-CapEx items (like purchases of financial investments/fixed deposits) that would distort the FCF number if subtracted wholesale.
- **Before calculating any multi-year growth rate, check for one-off items** — a large "other income," "exceptional item," or "discontinued operations" entry in any single quarter can make a multi-year profit CAGR meaningless. If you find one, calculate the growth rate both with and without it, and say so clearly.
- **Be suspicious of growth rates calculated off a very low or negative base year** — a large percentage CAGR off a near-zero starting point is not a meaningful comparison to normal growth and should be flagged as such, not presented at face value.
- **Don't mistake EBITDA for Free Cash Flow**, especially for a company in a heavy capital-expenditure phase. A business that must keep reinvesting heavily just to stay competitive (e.g. buying new hardware, expanding capacity) can show great EBITDA in a given quarter while genuinely generating very little or negative free cash — check this explicitly whenever a company is mid-expansion.

**End Stage 2 with a scorecard**: pass/fail/flag for each of the 10 points, followed by a plain-language summary of the most important findings.

---

## Stage 3 — Intrinsic Value (DCF)

Follow this exact sequence.

**Step 1 — Base Free Cash Flow.** Average the last 3 years' FCF. If the most recent year looks like an outlier (e.g. an unusually heavy capex year, or a one-off loss), also calculate an alternate average excluding it, and show both. As a sanity check, also calculate the company's historical FCF-to-Net-Profit ratio (average of the last several years) and apply it to TTM Net Profit — see if it lands near your Step 1 number.

**Step 2 — Growth rate.** Use a 2-stage structure: a higher growth rate for years 1-5, tapering to a lower rate for years 6-10. Default to something conservative (e.g. 15% then 10%) unless there's strong evidence to go higher — and if you do use a more aggressive rate, also show the conservative case for comparison. Very few companies sustain FCF growth above ~20% for long, so treat anything higher as an aggressive scenario, not the base case.

**Step 3 — Discount rate.** Calculate this properly via CAPM:
```
Discount Rate = Risk-Free Rate + Beta × Equity Risk Premium
```
Use the current 10-year Indian government bond yield as the risk-free rate, an equity risk premium of roughly 4-4.5% for India, and the stock's actual Beta (check more than one source — Beta estimates can vary meaningfully depending on the time window used, and a low Beta on a genuinely volatile or story-driven stock can be misleading rather than more accurate).

**Step 4 — Project FCF forward.** Starting from the Step 1 base, compound forward year by year:
```
Next Year's FCF = This Year's FCF × (1 + growth rate for that year)
```
Produce the full 10-year table.

**Step 5 — Terminal Value.**
```
Terminal Value = Year-10 FCF × (1 + Terminal Growth Rate) / (Discount Rate − Terminal Growth Rate)
```
Keep the terminal growth rate low — 3-4% at most, and never equal to or above the discount rate. Also calculate an alternative terminal value using a simple exit multiple on Year-10 FCF (e.g. 15x-25x), and show both methods side by side — they can diverge a lot, especially for growth/story stocks.

**Step 6 — Discount everything back to today.**
```
Present Value of Year N's FCF = Year N's FCF ÷ (1 + Discount Rate)^N
```
Do this for each of the 10 projected years and for the Terminal Value, then add them all up to get the Sum of Present Value of Cash Flows.

**Step 7 — Adjust for Net Debt.**
```
Net Debt = Total Debt − (Cash & Equivalents + Current Investments)
Intrinsic Share Price = (Sum of PV of Cash Flows − Net Debt) ÷ Total Shares Outstanding
```
Note: if Net Debt is negative (i.e. net cash), it gets added rather than subtracted. If you can't cleanly isolate the cash figure from the balance sheet, say so, and show how much the final answer changes across a plausible cash range rather than presenting one falsely precise number.

**Step 8 — Build the value band and margin of safety.**
```
Upper Band = Intrinsic Value × 1.10
Lower Band = Intrinsic Value × 0.90
Margin-of-Safety Buy Price = Lower Band × 0.70
```
Compare the current market price against this band and state clearly whether the stock looks undervalued, fairly valued, or overvalued.

**Step 9 — Reverse-engineer the market price.** Using the same growth/discount/terminal assumptions, solve backward: what "Year 0" FCF would the current market price actually require? Compare that number to the company's actual TTM Net Profit and Revenue. If the market price implies a free cash flow far beyond anything the company has ever produced, say so plainly — that's an important part of the answer, not a footnote.

**Step 10 — Show sensitivity.** Re-run the calculation across a reasonable range of discount rates, terminal growth rates, and FCF base assumptions, so it's clear how much the final answer depends on the choices made, rather than presenting a single number as if it were precise.

---

## General rules to follow throughout

- Never silently pick one assumption when more than one is reasonable — show the range, and clearly label which one is being used as the primary case.
- Always show your work — every intermediate number, not just the final answer.
- Keep this analysis separate from short-term price/momentum signals (GMP, technical indicators, subscription data). This process answers "what is the business worth," not "will the price go up soon."
- Don't issue a buy/sell recommendation. State clearly where the current price sits relative to the calculated value and band, and leave the decision to the person reading it.