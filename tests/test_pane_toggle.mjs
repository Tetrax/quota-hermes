// Unit tests for the quota pane toggle state machine embedded in
// desktop/plugin.js. The widget cannot be imported in plain Node (it imports
// @hermes/plugin-sdk), so the pure nextToggle() function is delimited by
// stable PANE-TOGGLE markers and exercised exactly as shipped.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const source = readFileSync(
	fileURLToPath(new URL("../desktop/plugin.js", import.meta.url)),
	"utf8",
);
const match = source.match(
	/\/\/ PANE-TOGGLE-START[^\n]*\n([\s\S]*?)\n\/\/ PANE-TOGGLE-END/,
);
assert.ok(match, "PANE-TOGGLE block must exist in desktop/plugin.js");
const nextToggle = new Function(`${match[1]}\nreturn nextToggle;`)();

const CHIP_TARGETS = ["ctx", "quota", "openai-codex", "deepseek"];

test("closed pane opens on any chip click", () => {
	for (const target of CHIP_TARGETS) {
		const next = nextToggle({ open: false, target: null }, target);
		assert.deepEqual(next, { open: true, target });
	}
});

test("second click on the same chip closes the pane", () => {
	for (const target of CHIP_TARGETS) {
		let state = nextToggle({ open: false, target: null }, target);
		state = nextToggle(state, target);
		assert.deepEqual(state, { open: false, target: null }, `target=${target}`);
	}
});

test("clicking another chip retargets without closing the pane", () => {
	const pairs = [
		["ctx", "openai-codex"],
		["openai-codex", "deepseek"],
		["deepseek", "ctx"],
		["deepseek", "openai-codex"],
		["openai-codex", "quota"],
	];
	for (const [first, second] of pairs) {
		let state = nextToggle({ open: false, target: null }, first);
		state = nextToggle(state, second);
		assert.deepEqual(state, { open: true, target: second }, `${first}->${second}`);
	}
});

test("state-less call behaves like a closed pane", () => {
	assert.deepEqual(nextToggle(undefined, "quota"), { open: true, target: "quota" });
	assert.deepEqual(nextToggle(null, "ctx"), { open: true, target: "ctx" });
});

test("stale internal state never blocks reopening (dismissed/hidden pane)", () => {
	// The widget feeds REAL layout visibility into nextToggle, so a pane closed
	// through the shell reports open:false even when a stale internal flag said
	// otherwise — the very next click reopens it instead of no-oping.
	const s = nextToggle({ open: false, target: "ctx" }, "ctx");
	assert.deepEqual(s, { open: true, target: "ctx" });
});

test("full toggle cycle open -> retarget -> close -> open", () => {
	let state = nextToggle(undefined, "ctx");
	state = nextToggle(state, "openai-codex");
	state = nextToggle(state, "openai-codex");
	assert.deepEqual(state, { open: false, target: null });
	state = nextToggle(state, "deepseek");
	assert.deepEqual(state, { open: true, target: "deepseek" });
});
