# INTERVIEW_PREP — know this project cold

Read in order. Each file is self-contained; together they are the whole story of Pager-Zero
from the point of view of the person who built and ran it.

| file | what it gives you | read when |
|---|---|---|
| [01-what-it-is.md](01-what-it-is.md) | the 30-second, 2-minute and 5-minute pitches; what it can and cannot do | before any conversation about the project |
| [02-features.md](02-features.md) | every feature, one paragraph each, with the "why" | to answer "what did you build?" |
| [03-how-it-works.md](03-how-it-works.md) | how each feature is implemented — concepts, not code | to answer "how does X work?" and follow-ups |
| [04-problems-faced.md](04-problems-faced.md) | the real bugs and dead ends, what I learned | "what was hard?", "tell me about a bug" |
| [05-codebase-map.md](05-codebase-map.md) | file structure, what lives where, what we integrate with, how to extend it | "walk me through the code", "how would you add X?" |
| [06-questions-and-answers.md](06-questions-and-answers.md) | likely interview questions with crisp answers, including the hard/honest ones | the night before |

Numbers to remember: **6** incident families · **16** read tools + **7** mutating · **12** step cap ·
**95 / 95 / 95 %** (root cause / fix / verified, n = 20) · **~34 s and ~8 tool calls** per incident on a
free model · **5** real faults in the demo stack · **81** tests · **4** LLM providers tested
(Gemini, Groq, Anthropic, Ollama).

The project itself is documented for users in `../README.md` and `../docs/GUIDE.md`; this folder is
for *you*.
