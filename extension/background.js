// D198: while a focus session runs, the distracting sites are sent to a calm page instead.
// The study plan's pages say until when (content.js); the block ends then, or as soon as a page
// of the plan says no session runs. Rules are the browser's own (declarativeNetRequest): the
// extension never sees what is browsed.
const SITES = ["instagram.com", "tiktok.com", "facebook.com", "messenger.com", "web.whatsapp.com"];
const RULES = SITES.map((domain, i) => ({
  id: i + 1,
  priority: 1,
  action: { type: "redirect", redirect: { extensionPath: "/blocked.html" } },
  condition: { requestDomains: [domain], resourceTypes: ["main_frame"] },
}));
const IDS = RULES.map((r) => r.id);

async function block(until, back) {
  await chrome.declarativeNetRequest.updateDynamicRules({ removeRuleIds: IDS, addRules: RULES });
  await chrome.storage.local.set({ until, back });
  chrome.alarms.create("focus-end", { when: Date.parse(until) });
  // Tabs already open on those sites go to the calm page too.
  const tabs = await chrome.tabs.query({ url: chrome.runtime.getManifest().host_permissions });
  for (const tab of tabs) chrome.tabs.update(tab.id, { url: chrome.runtime.getURL("/blocked.html") });
}

async function unblock() {
  await chrome.declarativeNetRequest.updateDynamicRules({ removeRuleIds: IDS });
  await chrome.storage.local.remove(["until"]);
  chrome.alarms.clear("focus-end");
}

chrome.runtime.onMessage.addListener((message) => {
  if (!message || message.kind !== "focus") return;
  const until = String(message.until || "");
  const end = Date.parse(until);
  if (until && Number.isFinite(end) && end > Date.now()) block(until, String(message.back || ""));
  else unblock();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "focus-end") unblock();
});

// After the browser restarts: a block whose time has passed is lifted.
chrome.runtime.onStartup.addListener(async () => {
  const { until } = await chrome.storage.local.get("until");
  if (!until || Date.parse(until) <= Date.now()) unblock();
});
