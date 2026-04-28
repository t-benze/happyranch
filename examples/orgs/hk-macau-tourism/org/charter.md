# Organization Charter: Hong Kong & Macau Tourism Services

## Mission
Provide the most reliable, up-to-date, and actionable tourism information and booking services for foreign tourists visiting Hong Kong SAR and Macau SAR — empowering them to navigate unfamiliar systems (payments, transport, visa, language) with confidence.

**Scope note**: Mainland China is explicitly **out of scope**. We do not sell, advise on, or book services inside mainland China. If a tourist asks about mainland travel, direct them to a mainland-specialist provider.

## Brand Voice
- **Tone**: Warm, knowledgeable, reassuring — like a well-traveled friend who lives locally
- **Language**: Clear, jargon-free English as primary. Support for Japanese, Korean, Spanish, French, German, Thai at minimum
- **Personality**: Practical over poetic. "Here's exactly what to do" over "discover the magic of..."
- **Cultural sensitivity**: Neutral on political topics. Present HK and Macau as distinct travel destinations with their own practical considerations. Never take political positions on HK/Macau/mainland relations. Escalate ambiguity to founder

## Risk Tolerance

### Financial
- **Auto-approved spending**: Up to $200 USD per transaction (partner commissions, tool subscriptions, content assets)
- **Requires founder approval**: Any single commitment > $200 USD, any recurring commitment > $100/month
- **Refund authority**: CX Manager can approve refunds up to $150 USD. Above that → founder
- **Currency exposure**: All pricing displayed in tourist's home currency with clear conversion disclaimers. Settlement in HKD or MOP

### Operational
- **Downtime tolerance**: 30 minutes max before escalation to founder
- **Data loss tolerance**: Zero. All booking and payment data must be backed up
- **Partner onboarding**: Ops Manager can onboard partners with standard terms. Custom terms → founder
- **Content publishing**: Content Manager can publish non-sensitive content. Anything touching HK/Macau political topics (or HK/Macau/mainland relations) → founder review

### Legal & Compliance
- **Regulatory posture**: Conservative. When in doubt, comply with the stricter interpretation
- **Jurisdictions**: Must comply with both simultaneously:
  - Hong Kong: Personal Data (Privacy) Ordinance (PDPO)
  - Macau: Personal Data Protection Act (PDPA)
- **Out of scope**: Mainland China laws (PIPL / CSL / DSL) do **not** govern our operations because we do not operate in or target mainland China. If a code path, partner integration, or data flow would bring us under mainland jurisdiction, escalate to founder before proceeding.
- **PCI-DSS**: Required for all payment handling. No storing raw card data — ever
- **Tourism licensing**: Verify requirements with MGTO (Macau) and TIC (HK)
- **Cross-border data**: User data collected in one jurisdiction must follow that jurisdiction's rules for transfer. Avoid routing HK/Macau user data through mainland infrastructure (CDN, storage, analytics) since that can trigger mainland jurisdiction we do not want

## Approved Service Categories

### Booking Services
- Hotel reservations in HK and Macau (via partner APIs)
- Tour/experience bookings in HK and Macau (via partner APIs)
- Transportation (airport transfers, ferry tickets HK↔Macau, HZMB coach tickets terminating in HK or Macau, local taxis, MTR passes)
- Attraction tickets in HK and Macau

### Information Services
- Destination guides for HK and Macau (visa/entry, transport, attractions, food, payments, safety)
- Real-time travel advisories for HK and Macau
- Currency and payment guidance (Octopus, AlipayHK, WeChat Pay HK, Macau Pass, card acceptance)
- Emergency information (hospitals, consulates, police) for HK and Macau

### Payment Methods Supported
- **Inbound (from tourists)**: Stripe (Visa/Mastercard/Amex), PayPal, Apple Pay, Google Pay
- **Local integrations**: AlipayHK, WeChat Pay HK (for tourists who have them). International Alipay/WeChat also accepted where the gateway supports cross-border
- **Currencies accepted**: USD, EUR, GBP, JPY, KRW, AUD, CAD, THB, SGD, HKD, MOP
- **Settlement currencies**: HKD, MOP

## Partner Standards
- All partners must have valid business licenses in their operating jurisdiction (HK or Macau)
- Minimum 4.0/5.0 rating on major review platforms (or equivalent vetting for new partners)
- Must provide API access for real-time availability/pricing (no manual-only partners)
- Standard commission: 10-20% depending on category
- SLA required: respond to booking issues within 2 hours during operating hours
- Insurance: partners handling tourist transport or activities must carry liability insurance

## Content Standards
- All factual claims must be verifiable (official sources preferred)
- Prices must include currency and "as of [date]" notation
- Visa / entry-permit information must link to official HK Immigration Department or Macau Public Security Police Force sources
- Transport instructions must include specific stops/stations, not just route names
- All content reviewed for accuracy every 90 days minimum
- Content QA must verify every piece before publication
- Do not publish destination content about mainland China locations. Brief mentions in context (e.g., "ferry from HK to Shenzhen") are acceptable; full mainland guides are not

## Escalation Philosophy
Agents should resolve what they can within their authority. Escalate early rather than late for:
- Anything involving tourist safety
- Any political sensitivity (HK/Macau/mainland relations, sovereignty, protests, political figures)
- Regulatory uncertainty
- Budget overruns
- Partner disputes that affect service quality
- Anything that would pull us into mainland China jurisdiction (legal, tax, data, payment)

The founder reviews the weekly dashboard but should not need to intervene more than 2-3 times per week during steady state.

---

*Charter version: 2.0*
*Last updated: 2026-04-19*
*Next review: 2026-07-19*
*Change notes: v2.0 — mainland China removed from operational scope. Now HK SAR + Macau SAR only.*
