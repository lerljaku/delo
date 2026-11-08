import { CONFIG, esc, renderNav } from "./common.js";

renderNav("");
const REGIONS = { "eu-west-1": "the EU (Ireland)", "eu-central-1": "the EU (Frankfurt)", "eu-north-1": "the EU (Stockholm)" };
const operator = esc(CONFIG.operatorName || "the operator of Delo");
const region = esc(REGIONS[CONFIG.dataRegion] || CONFIG.dataRegion || "the EU");
const contact = CONFIG.privacyContact
  ? `<a href="mailto:${esc(CONFIG.privacyContact)}">${esc(CONFIG.privacyContact)}</a>`
  : "the site admins";

// Keep in sync with what the code does: backend/delo/privacy.py, storage.py and infra/.
document.getElementById("content").innerHTML = `
  <p class="secondary">Last updated 2 October 2026</p>

  <h2>Who is responsible</h2>
  <p>Delo is run by ${operator} (the data controller). For any privacy question or request, contact ${contact}.</p>

  <h2>What we process and why</h2>
  <ul>
    <li><strong>Tournament results</strong>: player names as written in the results, match results, and the ratings and
      statistics calculated from them. Tournament organizers and admins upload them. They are published on this site.
      We do this based on our legitimate interest in running a public rating site for the local Magic: The Gathering
      community (GDPR Art. 6(1)(f)). You can object at any time, see below.</li>
    <li><strong>Accounts</strong> (optional): your email address and password (stored by Amazon Cognito; we never see the
      password), the player profile you claim and the note you send with the claim. We use them to sign you in and to
      verify that a profile is yours (Art. 6(1)(b)).</li>
    <li><strong>Payments</strong> (memberships): handled by Stripe, which processes your card and billing details under its own
      privacy policy. We receive and store only your Stripe customer number, whether you have a membership, and its level
      (to fulfil the purchase, Art. 6(1)(b)). Paid members can also save private match notes (visible only
      to them) and a profile URL name. Anyone with a claimed profile can add a flair shown next to their name.</li>
    <li><strong>Donations</strong> by bank transfer (Support page): we see the payer's name, account and payment message in our
      bank statement, and use them only to mark the donor as a Supporter (Art. 6(1)(b)). Bank records are kept as accounting law requires.</li>
    <li><strong>Upcoming tournaments</strong> posted by members: the details they enter, deleted two days after the event.</li>
    <li><strong>Visitor statistics</strong>: when a page loads, we count a page view without cookies. To count each visitor only once
      a day, your IP address and browser identifier are combined with a secret key into an anonymous code that changes every day and
      can't be turned back into your address; the code is deleted within 2 days. We keep only daily totals: visitors, page views, which
      pages were viewed and which websites linked to us (Art. 6(1)(f), to see how the site is used). Not counted if your browser sends
      Do Not Track or Global Privacy Control.</li>
    <li><strong>Technical logs</strong>: error logs of the server, kept for 14 days, to keep the site running.</li>
  </ul>
  <p>We don't sell data, show ads or use tracking or analytics. The site stores your sign-in token and display
    preferences (ladder, format) in your browser's local storage, and the island you selected in one cookie, so it is
    preselected when you come back. None of these is ever used for tracking.
    The player page loads the chart library from cdn.jsdelivr.net, which receives your IP address like any website you visit.</p>

  <h2>Where the data is stored</h2>
  <p>On Amazon Web Services in ${region}. Amazon acts as our processor.</p>

  <h2>How long we keep it</h2>
  <ul>
    <li>Tournament results: as long as the site runs, unless you ask us to remove your name.</li>
    <li>Accounts: until you delete your account.</li>
    <li>Backups of the database are kept for up to 35 days, so deleted data disappears from them within that time.</li>
  </ul>

  <h2>Your rights</h2>
  <p>You have the right to access, correct and delete your data, to restrict or object to its processing, and to data
    portability. You can also complain to your data protection authority.</p>
  <ul>
    <li><strong>Hide your name</strong>: create an account, claim your player profile and, once an admin has verified it,
      hide your name on the <a href="account.html">account page</a>. You appear as “Hidden player” everywhere.</li>
    <li><strong>Remove your name from the results</strong>: contact ${contact}. We replace your name with
      “Deleted player” in every stored tournament, including the original uploads. Your matches stay anonymously, so
      other players' ratings don't change. Tournaments uploaded later are anonymized automatically. To be able to do
      that, we keep a code computed from your name (not the name itself), used only to recognize it in new uploads.</li>
    <li><strong>Delete your account</strong>: on the <a href="account.html">account page</a>. This deletes your email and
      login. Your player profile stays, because it comes from public results, but it is no longer linked to you.</li>
    <li><strong>Get a copy of your data</strong>: your results are public on your player page. Contact ${contact} for
      your account data.</li>
  </ul>`;
