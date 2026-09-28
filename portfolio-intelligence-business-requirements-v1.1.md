# Portfolio Intelligence
## Business requirements and investment approach

Version 1.1 · 23 September 2026  
Prepared for: Sectors hackathon  
Scope: Indonesian equities, Sectors API

## 1. Product objective

The product should help retail investors approach stock research and portfolio decisions with greater discipline. Its output should explain what matters, how it affects the investment case, and what deserves attention next.

For someone researching a company, that means understanding the business, the quality of its earnings, the expectations reflected in its price, and the main ways the investment could go wrong. For someone who already owns shares, it also means understanding how those risks add up across the portfolio.

Three judgments need to remain distinct throughout the product: business quality, valuation, and portfolio fit. A well-run company can be expensive. An inexpensive stock can have a weak balance sheet. An attractive investment can still be too large or too difficult to exit for a particular portfolio. The analysis should explain those distinctions clearly.

This document sets out the investment reasoning and expected behavior that should guide agent design and interaction. The solution architect is responsible for technical architecture, model providers, and infrastructure. Requirements that cannot be supported by the available data should be documented with the specific gap, its analytical impact, and a proposed reduction in scope for business review.

Sections 2–9 describe the initial product requirements. The appendices provide calculation conventions, review thresholds, and cases for business acceptance. This version replaces the earlier business handoff for this scope.

## 2. Intended users and use cases

A user should be able to start without owning any shares. The product should support three entry points:

| Entry point | Typical question | What the user should receive |
|---|---|---|
| Stock research | “What drives this company's performance and investment outlook?” | A research brief covering the business, financial quality, valuation context, risks, and questions worth pursuing |
| Watchlist or hypothetical portfolio | “How do these ideas compare?” or “What would these proposed weights imply?” | A comparison on consistent terms, with proposed exposures, concentration, and liquidity implications |
| Existing portfolio | “Which holdings or developments require attention?” | A prioritized review of material changes, portfolio exposures, liquidity, and developments affecting the original investment case |

For the first version, assume long-only IDX common equities and cash, measured in IDR. Positions and cost bases come from the user where needed. Keep cash visible in portfolio weights. Identify unsupported instruments explicitly. Execution, leverage, short selling, and derivatives are outside this release.

Company research can proceed with limited personal information. Portfolio conclusions require more context: holdings, valuation date, horizon, liquidity needs, and relevant limits. Additional information should be requested when it could change the answer. If the user's mandate is incomplete, the output should explain which portfolio questions remain unanswered; general company research can still proceed.

The user remains responsible for investment decisions. The product may compare alternatives and quantify the consequences of a proposed allocation. It should not turn a research finding into an automatic buy, sell, or position-sizing instruction.

## 3. How the analysis should work

### Start with the question

The first task is to establish what the user is trying to understand. A question about an unusual price move needs a different investigation from a full company review. A routine portfolio check should focus on changes since the previous review.

For portfolio work, establish whether the holdings are actual or hypothetical, the base currency, and the observation date. Collect the objective, horizon, cash needs, risk capacity, benchmark, exclusions, and concentration limits as they become relevant. Avoid making the user complete a full investment questionnaire before answering a basic research question.

### Understand the business and the reported numbers

Explain how the company earns money and identify the few variables that matter most: volumes, pricing, margins, capital intensity, funding costs, or another sector-specific driver. Then examine whether reported profits are supported by cash generation and a resilient balance sheet.

The analysis should distinguish recurring operations from one-off items, cyclical conditions from structural changes, and reported figures from normalized estimates. Every adjustment needs a reason and a link back to the original figure. If the data does not support normalization, the reported number should be retained with its limitation explained.

Choose the financial template by business model. Banks need asset-quality, funding, profitability, and capital measures. Industrial working-capital or net-debt-to-EBITDA rules should not be applied mechanically to them. The same principle applies to other businesses whose economics require a different template.

### Form a view on valuation and downside

Once the financial picture is credible, assess valuation. Explain the reference being used: comparable businesses, the company's own history, normalized earnings, or a cash-flow model. Peer comparisons need consistent definitions, periods, and business characteristics.

Where forecasts are supportable, show how the result changes under reasonable assumptions. Identify what appears to be priced in and which assumptions have the greatest effect on value. A low multiple can reflect weak economics or peak earnings; the agent should investigate that before describing the stock as inexpensive.

Every investment case should include its main downside, a plausible competing explanation, and evidence that would change the assessment. “The stock fell” is usually insufficient as a thesis-break condition. A loss of pricing power, deterioration in asset quality, or failure to generate the expected cash flow is much more useful.

### Translate the finding into portfolio consequences

For a held or proposed position, ask how much the issue matters to the overall portfolio. Review issuer, sector, controlling-group, and common economic exposures. Several tickers exposed to the same commodity cycle or corporate group may provide less diversification than the user expects.

Consider the size of the exposure and the ability to exit it. Include spread, transaction costs, taxes, and market impact when comparing implementation alternatives if those inputs are available. Otherwise identify those omissions before presenting a net outcome. Liquidity estimates should show both normal and stressed assumptions.

The reasoning should remain traceable from the original development through business performance, cash flow or financing, valuation, and portfolio exposure. If a link is uncertain, say where the uncertainty enters.

### Review the conclusion before presenting it

The independent reviewer checks whether the evidence supports the conclusion, whether the calculations are appropriate, and whether important limitations are visible. An unresolved disagreement should survive into the final answer when it matters to the user.

This sequence sets the dependencies for a conclusion. It does not require a full valuation model for every question. A narrow investigation can stop once it has answered the question with adequate evidence.

## 4. Responsibilities across the agents

The proposed organization has five main roles. Specialist functions can be implemented as separate agents or ordinary services, depending on architectural requirements. The responsibilities below should remain intact across implementation choices.

| Role | Owns | Expected handoff | Boundary |
|---|---|---|---|
| Chief Portfolio Intelligence Orchestrator | Defines the question, assigns relevant work, and brings the findings together | A concise answer with priorities, disagreements, and next review steps | Cannot approve its own answer or override the independent reviewer |
| Investment Research Lead | Company economics, financial quality, valuation, and the investment thesis | A supported investment view with downside, assumptions, and conditions that would change it | Does not decide a user's portfolio allocation |
| Portfolio Risk Lead | Exposure, concentration, liquidity, and scenario consequences | The largest portfolio vulnerabilities, their drivers, and any limit breaches | Does not treat a risk statistic as a complete investment decision |
| Market and Event Intelligence Lead | Price and volume changes, flows, filings, and corporate events | What changed, how unusual it is, and what needs further investigation | Does not infer intent or causality from trading patterns alone |
| Independent Risk and Evidence Officer | Evidence quality, calculation validity, and release approval | A review decision with specific issues and required corrections | Cannot soften a finding to accommodate the Chief's preferred conclusion |

The independent reviewer needs a direct escalation route to the human responsible for the decision. Its review cannot be overruled by another agent. A separate role label is insufficient if the same workflow can silently bypass its decision.

### Chief Portfolio Intelligence Orchestrator

The Chief needs enough portfolio and investment knowledge to recognize the question, identify the relevant specialists, and understand why their answers may differ. Its contribution is judgment about relevance and synthesis.

For example, research may find improving earnings while risk identifies excessive exposure to the same sector. Both findings can be correct. The Chief should explain the trade-off rather than force the specialists into one positive or negative verdict.

Escalate when the answer depends on an unknown user constraint, a material disagreement remains unresolved, or the evidence cannot support the requested conclusion. Routine coordination should use modest reasoning capacity. More capable models should be reserved for difficult conflicts or analysis spanning several domains.

### Investment Research Lead and its specialist functions

This role needs knowledge of business economics, accounting, cash flow, capital allocation, valuation, and minority-shareholder considerations. Its work comprises four functions:

| Function | Analytical responsibility | Expected output |
|---|---|---|
| Fundamentals | Comparable financial periods, margins, cash conversion, balance-sheet strength, and sector-specific measures | The direction and quality of performance, with explanations for material changes and any adjustments |
| Valuation and expectations | Appropriate valuation methods, comparable businesses, implied expectations, and sensitivity to assumptions | A valuation range or relative context, with the assumptions that matter most |
| Ownership and governance | Control, free float, related-party exposure, dilution, and corporate-group relationships | A clear account of how ownership may affect minority economics, concentration, or liquidity |
| Thesis monitoring | New information against the previously recorded investment case | What strengthened or weakened the case, which assumption changed, and whether a stated failure condition occurred |

Unexplained earnings/cash-flow divergence, conflicting financial periods, opaque control, and valuation results that depend heavily on one assumption deserve explicit review. Governance concerns must be grounded in evidence; a relationship between shareholders is not evidence of wrongdoing.

The original thesis and its monitoring conditions should be retained. Any revision should record what changed and why. The record should distinguish a change supported by new evidence from a retrospective change in assessment criteria.

### Portfolio Risk Lead

This role needs portfolio-construction, concentration, liquidity, covariance, stress-testing, and benchmark knowledge. It should begin with reliably established exposures, then add statistical measures where the data supports them.

The output should identify the largest sources of vulnerability and explain their practical significance. Volatility, permanent capital loss, refinancing risk, and an inability to exit are different problems. A low historical volatility number should not dilute a warning about a weak balance sheet or an illiquid holding.

Scenarios should state the shock, horizon, transmission mechanism, affected holdings, and valuation method. A user-supplied price shock should be distinguished from an estimated response to a macro shock. Where the transmission from rupiah depreciation to a company's earnings or share price lacks supporting evidence, the exposure should be described qualitatively and the sensitivity left unestimated.

Escalate mandate breaches, materially illiquid positions, missing exposure mappings, and results that change substantially under reasonable modeling choices. Advanced portfolio models belong in a later release unless their inputs and validation are ready.

### Market and Event Intelligence Lead

This role needs market-microstructure knowledge, familiarity with corporate actions and filings, and disciplined interpretation of price, volume, foreign flow, and broker activity.

For an unusual move, establish its size, observation window, and comparison baseline. Check the market, sector, relevant disclosures, and corporate-action treatment. Describe what is observed separately from explanations that still need confirmation.

A flow anomaly can justify further research. It cannot by itself establish value, insider information, coordinated trading, or manipulation. Events affecting control, financing, dilution, operations, or reported results should be routed to the relevant research function and, for held names, to portfolio risk.

### Independent Risk and Evidence Officer

This role needs accounting and calculation literacy, an understanding of model limitations, and the ability to challenge causal reasoning. It should be able to follow a material claim back to its source and reproduce the relevant calculation.

Its review covers the identity and dates of the evidence, comparability of inputs, formula choice, unsupported assumptions, omitted adverse evidence, and consistency with the user's mandate. It must return a specific correction or limitation when it finds a problem.

| Review decision | Meaning for the user-facing answer |
|---|---|
| PASS | Release the answer as written |
| PASS WITH LIMITATIONS | Release with the relevant limitations visible beside the conclusion |
| REVISE | Correct the analysis and resubmit it |
| DATA BLOCKED | Withhold the affected conclusion and explain the missing or conflicting evidence |
| HUMAN ESCALATION | Explain the issue and the decision or clarification required from the user |

A blocked calculation need not prevent unrelated, supported findings from being shown. It must prevent those findings from being presented as a complete answer. Changes to the analysis require a fresh review of the affected conclusions.

## 5. Evidence and data expectations

Use Sectors as the primary data source. Confirm the actual fields, coverage, dates, and definitions before committing to a feature. The categories below describe what the business needs; they are not a claim that every field is available through the current subscription or interface.

| Analysis | Evidence needed |
|---|---|
| Company and financial review | Business description, financial statements, reporting periods, and consolidation basis |
| Valuation | Price, shares, comparable metrics, and any balance-sheet or forecast inputs required by the chosen method |
| Portfolio and liquidity | User positions and cash, dated prices, traded value, and available free-float information |
| Ownership and group exposure | Dated holdings, control relationships, and a basis for each group mapping |
| Events and market signals | Dated disclosures or events, price/volume history, and consistent flow or broker definitions |

For every material claim, retain its source, observation period, publication date where available, and retrieval date. A recently retrieved quarterly statement is still a quarterly observation. Align periods, currency, consolidation, and corporate-action adjustments before calculating comparisons.

The answer should make it possible to distinguish reported facts, calculated values, assumptions, interpretations, and judgments. Ordinary phrases such as “the company reported,” “the calculation indicates,” “assuming,” and “this suggests” can convey these distinctions. Internal classification labels should not be necessary to follow the argument.

When two sources disagree, first check definitions, reporting periods, revisions, and restatements. Preserve the unresolved difference if it matters. Missing data is not zero, and missing evidence is not proof that a risk is absent.

Macro inputs require the same discipline. Use a verified source within the agreed data scope or label the input as a scenario assumption. “Assume a 10% rupiah depreciation” is a valid scenario input; it does not establish how any particular stock will respond.

## 6. When the system should ask for attention

Keep the seriousness of an issue separate from confidence in the evidence. A potentially severe problem supported by weak evidence may deserve urgent verification. Averaging the two into one reassuring score would obscure the decision the user needs to make.

Prioritize according to the exposure affected, the possible consequence, urgency, and difficulty of responding. Appendix B provides initial review thresholds. They are demonstration settings for this version and should be visible whenever used. User-approved mandate limits take precedence.

A suspension of a held security, credible refinancing distress, material dilution, a serious reporting concern, or a breach of an explicit portfolio limit should receive direct attention regardless of any composite score. State the evidence and uncertainty. The response is a review or escalation; the system does not execute a transaction.

Ordinary monitoring should use four attention levels: Normal, Monitor, Investigate, and High Attention. No alert is needed when there is no material change. Data-quality warnings should remain separate so a user can distinguish a deteriorating investment from an incomplete analysis.

For a recorded thesis, use Supported, Weakened, Invalidated, or Unresolved, with an explanation of the evidence that changed it. A new idea can be marked Thesis Forming. “Supported” says something about the thesis; valuation and portfolio fit still need their own assessment.

## 7. What a good answer looks like

Lead with the finding and its significance. Follow with the evidence, the main uncertainty, and the next useful review step. Include portfolio implications when holdings are supplied. A short question should receive a proportionate answer; a full research request should include the business, financial quality, valuation, downside, and thesis-monitoring conditions.

The following example illustrates the expected tone and reasoning in a portfolio review. All figures are illustrative:

> The proposed allocation would raise this company to 18% of the portfolio, above the 15% High Attention review threshold. Its earnings outlook may be improving, but the larger position would also increase exposure to a sector that already accounts for 38% of the portfolio.
>
> Under the stated scenario of a 25% decline in this stock, the position would contribute a 4.5 percentage-point decline to portfolio value, before changes in other holdings. This is a sensitivity calculation, not a forecast.
>
> Assessing portfolio fit requires the user's concentration limit and near-term cash requirements. Exit liquidity also remains unassessed because traded-value history was not supplied. The company research can proceed, but the proposed allocation needs further review.

The important feature is that the user can see how the conclusion follows from the inputs. Limitations belong beside the conclusion they affect.

## 8. Keep computation and reasoning proportionate

Use ordinary code for arithmetic, ratios, exposure aggregation, thresholds, and data checks. Use statistics for historical comparisons and anomaly detection. A language model should interpret the validated results and explain their relevance.

Traditional ML is appropriate for a repeatable prediction or classification task with sufficient observations and a measurable improvement over a simple baseline. Adoption requires time-aware validation, leakage checks, error or calibration analysis, stability checks, and a fallback. A sophisticated model with weak labels is unlikely to improve this product.

For language models, match capability to the task. Extraction and routine classification generally require less reasoning than a disputed investment thesis or a complex scenario. Seniority in the agent hierarchy alone should not determine model cost.

Before spending on another data retrieval or reasoning pass, ask whether it could change the answer, the confidence, the review status, or a material risk finding. Stop when the question is answered or the remaining gap requires user input or unavailable evidence. Routine monitoring should reuse valid prior work and investigate changes. It should not regenerate a complete company dossier on every run.

## 9. Build priorities and the handoff

The initial priority is one complete, credible user journey, with all three entry points sharing the same analytical foundations.

| Priority | Business scope |
|---|---|
| Must for the first demonstration | Research a company without holdings; accept a watchlist or proposed/actual portfolio; calculate weights, name/sector concentration, and liquidity where supported; identify material changes; provide dated evidence; apply independent review; explain missing data |
| Should, if data and time permit | Peer comparison, group exposure, stored thesis monitoring, simple price-shock scenarios, and corroborated event or flow investigation |
| Later | Full forecast-driven DCF and reverse DCF, factor risk, VaR/ES, macro-response models, portfolio optimization, and trained predictive ML |

The appendix defines conventions for some later capabilities so they can be added consistently. Their presence does not make them hackathon deliverables. If group mapping or another requested field is unavailable, show the limitation rather than fabricate coverage.

For the demonstration, start with a user researching a stock, then add it to a hypothetical portfolio and show how the portfolio context changes the discussion. Introduce a material development and show the research update, risk assessment, and independent review. At least one case should demonstrate a conclusion being withheld because the evidence is insufficient.

The architecture handoff should include a proposed design, a mapping from business responsibilities to components and data sources, and any assumptions that change the intended behavior. The portfolio-management owner is responsible for investment rules, review thresholds, and business acceptance. The solution architect is responsible for technical implementation and operational budgets. The demonstration universe, data coverage, and scope adjustments require agreement between both owners before implementation is finalized.

The remaining business decisions are the initial demonstration names and sectors, whether any user mandate replaces the illustrative thresholds, and which optional features the verified data can support. Changes to authority, calculations, or policy should be recorded in the next version of this document.

## Appendix A. Calculation conventions

Use these conventions when implementing the relevant feature. Retain inputs, units, dates, and the formula version. Use `Unavailable` for missing inputs and `NM` for an economically meaningless ratio; neither should become zero.

### Portfolio value, exposure, and concentration

For the initial long-only scope:

```text
Position value_i = Shares_i × Price_i
Portfolio value = Σ Position value_i + Cash
Weight_i = Position value_i / Portfolio value
Category exposure_k = Σ_i Weight_i × Exposure loading_i,k
HHI = Σ_i Weight_i²
Effective number of holdings = 1 / HHI
```

Total value must be positive. State whether HHI includes cash or uses equity weights renormalized to 100%; do not compare the two conventions. HHI does not measure correlation or liquidity. Group and economic mappings need evidence; otherwise report the available issuer classifications and the coverage gap. Missing prices for material positions block full-portfolio weights unless an explicitly dated valuation assumption has been agreed.

### Returns and drawdowns

```text
Simple return_t = Adjusted price_t / Adjusted price_(t-1) - 1
Portfolio return_t = Σ_i Weight_(i,t-1) × Return_(i,t)
Annualized volatility = stdev(Return_t) × sqrt(Periods per year)
Drawdown_t = Wealth index_t / Historical peak wealth index_t - 1
Maximum drawdown = minimum(Drawdown_t)
```

Use a cash-flow-adjusted wealth index for portfolio drawdown so deposits and withdrawals do not appear as performance. Beginning weights apply to the corresponding return interval; intraperiod transactions require appropriate subperiod treatment. Label price returns when dividends are unavailable. Specify the observation window and frequency, corporate-action adjustments, and annualization convention.

### Liquidity

For this product, `ADV20` means median daily traded value, despite the common use of “average” in that abbreviation. Label it “20-session median traded value” in the output.

```text
ADV20 = median(Daily traded value across 20 scheduled market sessions)
Normal exit days_i = Position value_i / (ADV20_i × Participation rate)
Stressed traded value_i = ADV20_i × Retained liquidity fraction
Stressed exit days_i = Position value_i / (Stressed traded value_i × Participation rate)
Free-float capacity_i = Position shares_i / Free-float shares_i
```

Include genuine zero-trading sessions; do not confuse them with missing observations or silently replace them with older active days. A suspended stock has no currently executable exit estimate. A zero denominator means the exit estimate is unavailable, not zero days.

Participation and retained-liquidity fractions are assumptions in `(0,1]`. Proposed demo assumptions are 10% participation and 50% retained liquidity under stress, subject to business-owner acceptance. These approximate capacity; they do not promise execution at the observed price. Show individual illiquid holdings rather than relying on a portfolio average.

### Fundamentals

```text
Revenue growth = Revenue_t / Revenue_comparable_prior - 1
Operating margin = Operating profit / Revenue
Net margin = Net income / Revenue
ROA = Net income / Average total assets
ROE = Net income attributable to common shareholders / Average common equity
Net debt = Interest-bearing debt - Cash and cash equivalents
Net debt / EBITDA = Net debt / EBITDA
Interest coverage = EBIT / Interest expense
CFO margin = Operating cash flow / Revenue
Cash conversion = Operating cash flow / Net income
```

Use comparable periods and definitions. Annualize flow-to-stock ratios only with a stated convention. Nonpositive or immaterial denominators require review: negative equity makes ordinary ROE unsuitable; nonpositive EBITDA makes debt/EBITDA `NM`; nonpositive profit makes cash conversion `NM`. For the latter, discuss operating cash flow, cash burn, and funding needs instead. Interest coverage requires positive, consistently defined interest expense. Normalized values must reconcile to reported values.

For banks, use source-defined measures such as NIM, NPL, coverage, loan-to-deposit, cost-to-income, and capital adequacy. Record the source convention. Typical relationships are:

```text
NIM = Net interest income / Average earning assets
Gross NPL ratio = Non-performing loans / Gross loans
Coverage = Applicable loan-loss reserves / Non-performing loans
Loan-to-deposit = Loans / Deposits
Cost-to-income = Operating expense / Operating income
Capital adequacy = Eligible regulatory capital / Risk-weighted assets
```

Exact inclusions and regulatory definitions may differ. Do not recreate a reported bank ratio from incompatible components or present it as comparable when its convention is unknown.

### Valuation

```text
P/E = Common equity market value / Earnings attributable to common shareholders
P/B = Common equity market value / Common book equity
EV = Common equity market value + Debt + Preferred equity + Noncontrolling interests - Cash
EV/EBITDA = EV / EBITDA
FCFF = EBIT × (1 - Tax rate) + D&A - Capex - Change in operating NWC
FCFE = Net income + D&A - Capex - Change in operating NWC + Net borrowing
Terminal value_FCFF = FCFF_(n+1) / (WACC - g)
Terminal value_FCFE = FCFE_(n+1) / (Cost of equity - g)
```

Discount FCFF at WACC, then reconcile enterprise value to common equity, including relevant nonoperating items and claims. Discount FCFE at cost of equity. Terminal value is measured at the end of year `n` and must also be discounted. The discount rate must exceed `g`; currency, inflation basis, and cash-flow assumptions must agree. Define debt, cash availability, lease treatment, and noncontrolling interests consistently with the earnings measure.

Negative or near-zero earnings and negative book equity make ordinary multiple comparisons unsuitable. Show sensitivities and identify dependence on terminal value. Forecast-driven valuation remains a later feature until the assumptions and data can be supported.

### Flow and broker activity

```text
Normalized foreign flow = Net foreign traded value over stated window / ADV20
Broker gross value_b = Buy value_b + Sell value_b
Top-K broker gross share = Σ_top-K Broker gross value_b / Σ_all Broker gross value_b
Broker net imbalance = Σ_all abs(Buy value_b - Sell value_b) / Σ_all Broker gross value_b
```

Use the same market, coverage, units, and time window for broker numerators and denominators; define `K`. Summing both sides counts each market trade twice when coverage is complete, so do not divide that numerator by single-sided exchange turnover. Partial broker coverage must be labeled as such. Normalized foreign flow above is expressed in daily-traded-value equivalents, not as a percentage of window turnover. None of these measures establishes trading intent.

### Scenario and statistical risk — when implemented

```text
Scenario portfolio return = Σ_i Weight_i × Scenario return_i
Scenario loss fraction = -Scenario portfolio return
Scenario currency P&L = Portfolio value × Scenario portfolio return
Portfolio variance = w'Σw
Portfolio volatility = sqrt(w'Σw)
Derivative of portfolio variance with respect to w_i = 2 × (Σw)_i
Allocated variance contribution_i = w_i × (Σw)_i
Percentage variance contribution_i = Allocated variance contribution_i / Portfolio variance
Beta_i = Cov(Return_i, Benchmark return) / Var(Benchmark return)
```

Scenario aggregation assumes fixed starting weights over the stated scenario horizon and consistently defined asset returns. State the cash assumption and any omitted costs or liquidity effects. Macro transmission requires separate support. Reverse stress asks which conditions would breach a user-defined loss or liquidity threshold; it does not assign a probability to those conditions.

For covariance and beta, disclose sample length, frequency, missing-data treatment, and estimator. Consider shrinkage when the sample is weak relative to the number of assets. Zero variance makes percentage contributions or beta undefined. Historical relationships may fail under stress.

If VaR/ES is added, define loss as negative return, VaR as the selected loss quantile, and ES as the average of the worst tail of losses. For `N` equally weighted observations and confidence `α`, sort losses from worst to best, let `q=N(1-α)` and `k=floor(q)`, and calculate:

```text
ES_α = [Sum of worst k losses + (q-k) × Next-worst loss] / q
```

Omit the fractional term when `q` is an integer. Disclose horizon, quantile convention, sample, and tail observation count. An estimate based on very few tail observations requires an explicit reliability warning. These statistics supplement the business, liquidity, and scenario analysis.

## Appendix B. Initial review settings

These settings are illustrative product choices, not validated suitability limits. Use the highest threshold exceeded and show the user's mandate instead where one exists.

| Measure | Monitor | High Attention | Critical issue requiring human review |
|---|---:|---:|---:|
| Single-name portfolio weight | >10% | >15% | >20% |
| Sector weight | >30% | >40% | >50% |
| Controlling-group weight | >15% | >25% | >35% |
| Stressed exit time | >2 sessions | >5 sessions | >10 sessions |

Critical is an escalation designation within High Attention, not a trading instruction. A suspension overrides historical liquidity calculations. An explicit hard mandate breach also escalates directly, even if it sits below a demonstration threshold.

For a simple initial price trigger, investigate an absolute daily move above 7%. Show the market and sector comparison. Flag drawdowns of at least 10%, 20%, and 30% as Monitor, Investigate, and High Attention respectively, using the highest applicable band. The default review window is 60 market sessions; show available history if shorter. Check adjustments and events before interpreting any trigger.

For fundamentals, investigate deterioration over two comparable periods, repeated positive profits with negative operating cash flow, unexplained margin compression, weakening debt coverage, or relevant sector-specific deterioration. An individually material event need not wait for a second period.

If a numeric priority score is useful internally, the earlier illustrative method can be retained:

```text
Materiality score = 25 × (0.35E + 0.35S + 0.15U + 0.15M)
```

Each input ranges from 0 to 4: exposure (`E`), severity (`S`), urgency (`U`), and difficulty of responding (`M`). Record the reason for each grade. Scores below 25, 50, and 75 are Low, Moderate, and High respectively; 75–100 is Critical. In research mode, exposure means relevance to the company or thesis. This is an uncalibrated sorting aid; direct escalation rules override it. It is optional for the demonstration.

Keep evidence quality alongside materiality: strong when direct, relevant, comparable evidence supports the finding; medium when meaningful limitations remain; weak when indirect or contested; insufficient when the conclusion cannot be supported. High materiality with weak evidence means urgent verification. Insufficient evidence blocks the dependent conclusion, while the possible consequence can still be described conditionally.

## Appendix C. Business acceptance cases

These cases describe expected behavior. The architect can translate them into suitable tests. Later-feature cases apply when that feature is enabled.

| Case | Expected behavior |
|---|---|
| User has no holdings | Provide company research without requiring a portfolio |
| Price rises while earnings and cash generation deteriorate | Keep the market observation separate from the weakening fundamentals |
| Price falls alongside foreign outflow | Report both observations, investigate context, and avoid claiming proven causality |
| Company looks attractive but proposed weight is excessive | Preserve the research view and flag the allocation concern separately |
| Several holdings share a controller | Aggregate supported group exposure and disclose unmapped holdings |
| Severe potential event has weak evidence | Prioritize verification and describe the possible effect conditionally |
| Material holding has no usable valuation price | Withhold complete portfolio weights and dependent risk conclusions |
| Bank is supplied to an industrial template | Select appropriate measures or mark the calculation unsupported |
| Positive earnings are repeatedly unsupported by cash | Raise an earnings-quality question and examine the cause |
| Net income is negative | Return NM for cash conversion and discuss cash generation and funding |
| Low P/E reflects peak-cycle earnings | Explain the normalization issue before judging valuation |
| Broker concentration increases | Describe the measured activity without allegations about intent |
| Requested macro observation is unavailable | Identify the gap or use an explicitly labeled scenario assumption |
| Specialists disagree | Preserve the material disagreement and escalate what cannot be resolved |
| Inputs, formulas, and policy are unchanged | Produce identical calculations and consistent statuses; wording may vary |
| FCFF is discounted at cost of equity, or terminal growth meets/exceeds the discount rate | Reject the valuation and identify the required correction |
| A deposit increases portfolio value | Do not report the deposit as investment return or drawdown recovery |
| No material change occurs during monitoring | Record the check without generating a fresh alert |

Each applicable case should produce a conclusion, supporting evidence, and a review decision. These outputs form the basis for business acceptance before the demonstration.
