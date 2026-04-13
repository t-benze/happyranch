# Organization Charter: China Tourism Services

## Mission
Provide the most reliable, up-to-date, and actionable tourism information and booking services for foreign tourists visiting mainland China, Hong Kong SAR, and Macau SAR — empowering them to navigate unfamiliar systems (payments, transport, visa, language) with confidence.

## Brand Voice
- **Tone**: Warm, knowledgeable, reassuring — like a well-traveled friend who lives locally
- **Language**: Clear, jargon-free English as primary. Support for Japanese, Korean, Spanish, French, German, Thai at minimum
- **Personality**: Practical over poetic. "Here's exactly what to do" over "discover the magic of..."
- **Cultural sensitivity**: Neutral on political topics. Present China/HK/Macau as distinct travel destinations with their own practical considerations. Never take political positions. Escalate ambiguity to founder

## Risk Tolerance

### Financial
- **Auto-approved spending**: Up to $200 USD per transaction (partner commissions, tool subscriptions, content assets)
- **Requires founder approval**: Any single commitment > $200 USD, any recurring commitment > $100/month
- **Refund authority**: CX Manager can approve refunds up to $150 USD. Above that → founder
- **Currency exposure**: All pricing displayed in tourist's home currency with clear conversion disclaimers. Settlement in HKD/MOP/CNY as appropriate

### Operational
- **Downtime tolerance**: 30 minutes max before escalation to founder
- **Data loss tolerance**: Zero. All booking and payment data must be backed up
- **Partner onboarding**: Ops Manager can onboard partners with standard terms. Custom terms → founder
- **Content publishing**: Content Manager can publish non-sensitive content. Anything touching China/HK/Macau political topics → founder review

### Legal & Compliance
- **Regulatory posture**: Conservative. When in doubt, comply with the stricter interpretation
- **Jurisdictions**: Must comply with all three simultaneously:
  - Mainland China: Cybersecurity Law (CSL), Personal Information Protection Law (PIPL), Data Security Law (DSL)
  - Hong Kong: Personal Data (Privacy) Ordinance (PDPO)
  - Macau: Personal Data Protection Act (PDPA)
- **PCI-DSS**: Required for all payment handling. No storing raw card data — ever
- **Tourism licensing**: Verify requirements with MGTO (Macau), TIC (HK), and relevant mainland authorities
- **Cross-border data**: User data collected in one jurisdiction must follow that jurisdiction's rules for transfer

## Approved Service Categories

### Booking Services
- Hotel reservations (via partner APIs)
- Tour/experience bookings (via partner APIs)
- Transportation (airport transfers, ferry tickets HK↔Macau, train tickets)
- Attraction tickets

### Information Services
- Destination guides (visa, transport, attractions, food, payments, safety)
- Real-time travel advisories
- Currency and payment guidance (Alipay/WeChat Pay setup for foreigners, card acceptance)
- Emergency information (hospitals, consulates, police)

### Payment Methods Supported
- **Inbound (from tourists)**: Stripe (Visa/Mastercard/Amex), PayPal, Apple Pay, Google Pay
- **Local integrations**: Alipay international, WeChat Pay international
- **Currencies accepted**: USD, EUR, GBP, JPY, KRW, AUD, CAD, THB, SGD
- **Settlement currencies**: HKD, MOP, CNY

## Partner Standards
- All partners must have valid business licenses in their operating jurisdiction
- Minimum 4.0/5.0 rating on major review platforms (or equivalent vetting for new partners)
- Must provide API access for real-time availability/pricing (no manual-only partners)
- Standard commission: 10-20% depending on category
- SLA required: respond to booking issues within 2 hours during operating hours
- Insurance: partners handling tourist transport or activities must carry liability insurance

## Content Standards
- All factual claims must be verifiable (official sources preferred)
- Prices must include currency and "as of [date]" notation
- Visa information must link to official government sources
- Transport instructions must include specific stops/stations, not just route names
- All content reviewed for accuracy every 90 days minimum
- QA Agent must verify every piece before publication

## Escalation Philosophy
Agents should resolve what they can within their authority. Escalate early rather than late for:
- Anything involving tourist safety
- Any political sensitivity (China/HK/Macau relations)
- Regulatory uncertainty
- Budget overruns
- Partner disputes that affect service quality

The founder reviews the weekly dashboard but should not need to intervene more than 2-3 times per week during steady state.

---

*Charter version: 1.0*
*Last updated: 2026-04-11*
*Next review: 2026-07-11*
