# CatchUp

### Your AI second listener.

## Configuration templates

On the Configurations page, enter a template name and choose **Save template** to
save the Conversation Configuration and Custom Report together. Saved templates
include focus areas, topics and coverage criteria, participant roles, and report
sections with their instructions and field settings.

Each Custom Report section has a **Generate only in final report** checkbox.
Leave it unchecked for live checkpoint updates, or check it to generate that
section only after **Stop** or when analyzing an uploaded recording. Final-only
sections are excluded from intermediate report schemas and instructions; the
final report includes all sections. Topic coverage continues to update during
recording. This setting is saved with the template, and existing templates
default to updating every section during live checkpoints.

Realtime function calls can occasionally return incorrectly nested topic
coverage. CatchUpAI supplies a configuration-specific layout reference and
relocates unambiguous misplaced topic fields or nested topic IDs before
validation. It preserves the model's values and does not invent missing topics,
criteria or evidence. If local recovery cannot fix a structural error, a
background `gpt-5.4-mini` Responses call proposes field moves using strict
Structured Outputs. The app applies the moves, rejects overwrites or changes to
topic/criterion assignments, and verifies the original values before accepting
the report. Missing or truncated content needs a new Realtime analysis instead.
Each analysis allows one mini repair call and one Realtime validation retry;
only validated reports are displayed. A repair call times out after 12 seconds.

The fallback uses the existing `OPENAI_API_KEY`. Set `REPORT_REPAIR_MODEL` in
`.env` to change the repair model, or `REPORT_REPAIR_ENABLED=false` to disable
the mini fallback. Recording continues during repair. If recovery fails, the UI
shows a short notice and keeps the last valid report; technical diagnostics stay
in the server logs. A successful report clears earlier notices.

Under **CRM content**, choose **Add CRM content** and paste a record from your
CRM as a JSON object (up to 20,000 characters). CatchUpAI adds a **Record Check**
report section and, at each checkpoint, compares what was said with the record.
Each field is reported as Consistent, Differs or Not mentioned, with a short quote.
Differences are flagged for human review, not as conclusions. The record is saved
with the template. **Remove** deletes the record and its Record Check section. The
record is sent to OpenAI with the conversation, so only paste data you may share.

Select a saved template and choose **Load** to fill both editors. After editing,
choose **Save changes** to update the loaded template, or use a different, unique
name with **Save as new** to keep a separate copy. Each template in the dropdown
has an **×** button; confirm the popup to delete it. The current configuration
remains available in the editors. When creating a new template, **Reset** clears
both configuration editors and the template name.
Choose **Create new template** in the dropdown to leave a loaded template and
start with empty editors. **Save changes** is enabled only after editing the
loaded configuration or its name. Validation messages appear beside the relevant
configuration fields and below the template save controls.

Templates are stored as JSON files in `configuration_templates/` and remain
available after restarting the app. Template names must be unique.

Under **Topics to cover**, choose **Upload** to import topics from a PDF, Word,
text, Markdown, RTF or ODT document (up to 20 MB). Set the purpose first; only
guidance relevant to that purpose is extracted. Review the suggestions,
expand them to edit coverage criteria or see
their source, and remove unwanted topics before choosing **Add selected topics**.
Existing topics are retained; matching topic names receive any new criteria.
Cancel closes the review without applying its suggestions.
Topic lists scroll independently in the configuration page and upload review.
Use the **×** at the right of a topic or report section, then confirm removal;
choosing **Cancel** keeps the item.

Document extraction uses the OpenAI Responses API with strict Structured Outputs
and the existing `OPENAI_API_KEY`. The default model is `gpt-5.4-mini`; set
`TOPIC_EXTRACTION_MODEL` in `.env` to use another model supporting file input and
Structured Outputs. Documents are sent in full rather than clipped locally.

CatchUp listens to conversations and helps you understand what you may have missed.

## Cost estimation

Open **Cost Estimation** in the left sidebar to see costs for the current audio
analysis. The page refreshes during recording and retains the final estimate
after analysis ends. You can switch between Conversation Studio and Cost
Estimation while recording; the recorder stays available on both pages.

The dashboard shows the session total, number of analyses and savings from
caching. Its breakdown separates regular and cached audio, text instructions,
cached text, report generation and reasoning, with token counts and USD rates.
Reasoning is part of output usage and is counted once. When the API does not
report its token count separately, its charge stays in report generation and
the reasoning row says it is included.

Live recordings show each analysis interval, the final-report charge and a
running total. Select an interval to inspect its breakdown. Uploaded recordings
show one combined cost. Validation retries count toward the same analysis.
Interval numbers follow fixed audio-time checkpoints: with a 60-second setting,
Interval 4 is the 4:00 checkpoint. A request recovered at 4:24 still belongs to
Interval 4, and the next checkpoint remains 5:00. Reconnection attempts do not
advance the interval number; their received usage is grouped into that interval.
The table and CSV include the interval's time range and actual request position.
Existing session records are displayed using audio time even if they originally
stored request counters. If an outage or a slow response spans a whole checkpoint,
that time checkpoint can still be missed; audio is retained for recovery.
Mini repair calls, including paid responses whose repairs are rejected, also
count toward that analysis. Interval rows identify repair calls and the CSV
includes their count. Their input, output and reasoning tokens use the repair
model's rates; estimates show mixed rates when multiple models are involved.
**Download costs** exports the analysis costs as CSV. Missing usage or unknown
prices mark the estimate as partial; known charges remain visible. Starting a
new analysis or resetting the workspace clears the displayed session costs.

Cost and analysis warnings appear as notifications at the top right. Use the
close button to dismiss them; the same notice stays dismissed during live
refreshes and tab changes. A new recording can show its own warnings. The cost
dashboard retains its partial-estimate label when some charges are unknown.

API usage is printed in the server terminal and appended to `logs/api_usage.jsonl`
(excluded from Git). Each response records its ID, logical session ID, model,
raw token usage, cached-token details, calculated USD cost, and the price list
used. Live, uploaded-recording, and document-extraction requests are covered.
Failed responses and validation retries count too; duplicate response IDs do not.
Browser reconnects retain the same session total. No audio, prompts, report
contents, or document contents are written to this usage log.

Realtime capture uses TCP keepalive and WebSocket pings every 15 seconds, with
45 seconds allowed for a matching pong. Socket reading and report processing
run separately so report validation cannot delay ping/pong handling. Request
rejections and local processing errors do not automatically close the socket.
Actual connection failures trigger the existing audio replay and backoff.
`realtime_connection` entries in `logs/api_usage.jsonl` record connection IDs,
close codes/reasons, error types, and pending checkpoint metadata. These entries
do not add charges. Missing usage is flagged when a response was still pending;
a disconnect between completed reports does not invent unknown charges.

`observed_cost_usd` is the sum of responses that could be priced. Missing usage
or unknown model prices produce `cost_usd: null` and an incomplete total; a lost
connection also marks the total incomplete because final usage may not arrive.
Prices in `catchupai/usage.py` were checked on 2026-10-12 and need updating when
OpenAI changes them. Calculated costs exclude taxes, credits, account discounts,
hosting, and currency conversion; reconcile against OpenAI billing for the final
charge. Run the offline checks with
`python -m unittest discover -s tests -p "test_*.py"`.

It identifies important points, unanswered questions, action items, key details, and useful suggestions — so you can stay focused on the conversation instead of worrying about remembering everything.

---

## 💡 What is CatchUp?

During a conversation, we often miss important details because we're busy thinking about what to say next.

**CatchUp acts as your second listener.**

It listens to the conversation, analyzes what was discussed, and gives you useful insights after or during the conversation.

> **You talk. CatchUp listens. You catch what you missed.**

---

## ✨ Key Features

### 🔍 What Did I Miss?

Find important points that may have been overlooked during the conversation.

### 📝 Key Takeaways

Get a simple summary of the most important things discussed.

### ❓ Questions You Could Ask

Discover useful follow-up questions that you may not have thought of.

### ✅ Action Items

Identify tasks, commitments, deadlines, and next steps mentioned during the conversation.

### 💡 Suggestions

Get AI-generated suggestions based on the context of the discussion.

### ⚠️ Things to Clarify

Find statements, decisions, or topics that may need further clarification.

### 🧠 Important Details

Keep track of names, dates, numbers, decisions, requirements, and other useful information.

---

## 🎯 Example

### Conversation

> **Person A:** We need to finish the website by Friday.
> **Person B:** Okay, I'll take care of it.

### CatchUp

**What You Missed**

- Testing responsibility wasn't clearly assigned.
- The Friday deadline was mentioned but the exact delivery time wasn't confirmed.

**You Could Ask**

- Who will handle the final testing?
- What time on Friday should the website be ready?

**Action Items**

- Confirm testing responsibility.
- Confirm the final delivery time.

---

## 🚀 How It Works

```text
Conversation
     ↓
Audio Capture
     ↓
Speech-to-Text
     ↓
AI Conversation Analysis
     ↓
Context Understanding
     ↓
Insights & Suggestions
     ↓
CatchUp
```

---

## 🧩 Core Concept

CatchUp is not just another meeting transcription tool.

The goal is to answer:

> **"What should I know that I didn't notice?"**

Instead of only telling users **what was said**, CatchUp focuses on **what the conversation means and what the user may have missed**.

---

## 🌍 Possible Use Cases

- 👨‍💼 Client conversations
- 💼 Business meetings
- 📞 Customer calls
- 🤝 Sales conversations
- 👨‍💻 Technical discussions
- 🎓 Classes and discussions
- 🎤 Interviews
- 🧑‍🤝‍🧑 Personal conversations
- 🗣️ Negotiations
- 📋 Requirements gathering

---

## 🔮 Future Ideas

- Real-time conversation insights
- Smart follow-up suggestions
- Conversation memory
- Speaker identification
- Sentiment and tone analysis
- Important-topic detection
- Contradiction detection
- Decision tracking
- Automatic follow-up message generation
- Integration with Slack, Teams, Salesforce, and email
- Personal conversation history
- Voice-based AI assistant

---

## 🔐 Privacy

Conversations can contain sensitive information.

CatchUp should be designed with privacy as a core principle:

- Clear recording indicators
- User-controlled recording
- Secure audio processing
- Minimal data retention
- Encryption
- Transparent AI processing
- Ability to delete conversation data

---

## 🛠️ Project Status

> 🚧 **Early Development**

CatchUp is currently being explored as an AI-powered conversation intelligence application.

---

## 🎯 Vision

**CatchUp aims to become the AI that listens when you can't listen to everything.**

You don't need to remember every word.

You just need to know **what matters**.

---

## 📄 License

License information will be added as the project develops.
