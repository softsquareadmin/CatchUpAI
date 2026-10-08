# CatchUp

### Your AI second listener.

## Configuration templates

On the Configurations page, enter a template name and choose **Save template** to
save the Conversation Configuration and Custom Report together. Saved templates
include focus areas, topics and coverage criteria, participant roles, and report
sections with their instructions and field settings.

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
