# Design Heuristics Review

Nielsen's 10 usability heuristics applied to the Houses web app design.

---

## 1. Visibility of System Status

**✅ Good:** Enrichment progress screen shows each module with ✓/◌/○ states (completed/in-progress/pending). Users see exactly what's happening during the 15–60s wait.

**✅ Good:** Property cards on the list show monthly cost totals as a quick status indicator. EPC badges and commute pills use colour to signal quality at a glance.

**⚠️ Gap:** Property detail sections that are expanded/collapsed use ▼/▶ chevrons, but there's no visual indicator of **loading** when lazy-loading section content via HTMX. Should add a shimmer/skeleton inside the section while `hx-get` resolves.

**⚠️ Gap:** When enrichment finishes, there's no notification — the user is just taken to the property detail. Consider a subtle toast or banner: "Enrichment complete. 6 modules updated."

**⚠️ Gap:** The "No" status dims a card to 55% opacity, but does the user know *why* it was rejected? The Status Reason field should be visible without tapping into detail.

## 2. Match Between System and the Real World

**✅ Good:** Domain language matches the spreadsheet and real-world house hunting: "Rightmove link", "EPC", "Ofsted", "stamp duty", "commute", "viewing". No technical jargon in the UI.

**✅ Good:** Commute is shown as "Simon → Victoria" with a route breakdown, matching how people naturally describe their commute ("I take the Bakerloo to Oxford Circus then Victoria line").

**✅ Good:** Rating semantics (green/orange/red) match the existing spreadsheet conditional formatting. Users familiar with the sheet will feel at home.

## 3. User Control and Freedom

**✅ Good:** "← Back" in the header on every detail/config/add screen. Standard navigation pattern.

**⚠️ Gap:** There's no "undo" for status changes. If a user accidentally taps "No" on a property, there's no confirmation dialog. Should either:
- Add a confirmation: "Mark 48 Acacia Avenue as No? This will dim it from the list."
- Or make changes immediate but show an "Undo" toast for 5s.

## 4. Consistency and Standards

**✅ Good:** All interactive elements look consistent across screens. Cards use the same border-radius, shadows, and padding. Badges are always 22px tall with rounded corners. Buttons are always #1a2a3a.

**✅ Good:** Rating colours are consistent across domains — green is always good, orange always middling, red always bad — regardless of whether it's EPC, commute, Ofsted, or walk time.

**✅ Good:** Expandable sections all work the same way: tap header → toggle content. No special cases.

## 5. Error Prevention

**✅ Good:** The Add Property form checks for existing properties before enrichment starts (returns an error message). Prevents duplicates.

**⚠️ Gap:** No validation on the Rightmove URL input. Users might paste a non-Rightmove URL. Should validate the domain and show an inline error before submitting.

**⚠️ Gap:** On the User Config screen, there's no validation on financial inputs (negative deposit, unrealistic mortgage rate). Should clamp or warn.

## 6. Recognition Rather Than Recall

**✅ Good:** Property cards show all key info (EPC, commute, price, status) without needing to open the detail page. Users don't need to remember which property had what.

**✅ Good:** The summary bar at the top of the property detail repeats key info (EPC, price, status) so it's visible even as the user scrolls through sections.

**⚠️ Gap:** Commute pills use "S", "L", "B" labels. New users might not know who these refer to. Should use full names "Simon", "Lorena", "Bracknell" (or at least have a legend key accessible from the list).

## 7. Flexibility and Efficiency of Use

**✅ Good:** The expandable section pattern works for both casual browsing (scan summaries) and deep-diving (expand for full data). No separate "detail" and "summary" modes.

**✅ Good:** Status can be toggled directly from the property list (tap badge → dropdown). No need to enter the detail page for quick triage.

**⚠️ Gap:** No bulk actions. If a user wants to mark 3 properties as "No", they need to do them one at a time. Consider: swipe-to-dismiss on the list, or multi-select mode.

## 8. Aesthetic and Minimalist Design

**✅ Good:** Cards show only the information needed for triage (EPC, commute times, total cost, status). No extraneous data. The 40-column spreadsheet is reduced to ~8 digestible pieces per card.

**✅ Good:** White space is used generously — 16px card padding, 8px gaps, 24px section margins. The mobile view is not cramped despite showing lots of data.

**✅ Good:** The dimmed "No" cards visually recede, letting the active properties dominate visual attention.

## 9. Help Users Recognise, Diagnose, and Recover from Errors

**⚠️ Gap:** The enrichment progress screen shows which module failed and why ("TfL API returned 402"), but it's not clear what the user should *do* about it. Should add guidance: "This usually means the API key has run out of credits. Check the config."

**⚠️ Gap:** If the Google Sheet is unavailable, what does the user see? A generic "Failed to load properties." with a retry button. Should be more specific: "Spreadsheet not found. Check HOUSES_SHEET_ID."

## 10. Help and Documentation

**⚠️ Gap:** No onboarding or help. A new user (Simon or Lorena) won't know:
- What is a Rightmove ID?
- Where does the data come from?
- What do the colours mean?
- Why is Simon's commute different from Lorena's?

A minimal "About / Help" screen or a tooltip system would help non-technical users. At minimum, a "What do the colours mean?" legend accessible from the list view.

---

## Issues Addressed by Revised Design

| Issue | How it was resolved |
|---|---|
| EPC not important on list | Removed from card entirely |
| Price not prominent enough | Moved to right-aligned bold on line 1, used as proxy |
| Total cost often unknown | Line 5 adapts: shows total+delta, or price as proxy, or nothing |
| Increment vs current missing | Added "+£X vs now" to financial line |
| Missing schools/amenities not visible | Schools get their own line with dual-colour dots; walk to town shown on financial line |
| Commute labels unclear | Changed from S/L/B to full names Simon/Lorena/Bracknell |

## Summary: Priority Issues

| Priority | Issue | Screen | Fix |
|---|---|---|---|
| **Medium** | School dual-dot hover/tap content needs implementing | Property List | Tooltip shows Ofsted rating + year, walk time, accuracy warning |
| **Medium** | No loading indicator for expandable sections | Property Detail | Add skeleton shimmer during HTMX load |
| **Medium** | No URL validation on add form | Add Property | Validate domain, show inline error |
| **Medium** | No completion notification | Add Property | Toast after enrichment completes |
| **Low** | "Dismissed · tap to reactivate" text might be missed | Property List | Consider making entire dismissed card a tap target |
| **Low** | No onboarding / colour legend | Property List | Help screen or tooltip system |
