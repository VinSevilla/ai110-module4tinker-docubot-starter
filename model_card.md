# DocuBot Model Card

This model card is a short reflection on your DocuBot system. Fill it out after you have implemented retrieval and experimented with all three modes:

1. Naive LLM over full docs  
2. Retrieval only  
3. RAG (retrieval plus LLM)

Use clear, honest descriptions. It is fine if your system is imperfect.

---

## 1. System Overview

**What is DocuBot trying to do?**  
Describe the overall goal in 2 to 3 sentences.

> DocuBot answers developer questions about a small sample app (auth, API, database, setup) using only the markdown docs in `docs/`. The whole point is to compare three ways of answering the same question — just asking an LLM, just doing keyword search, and doing both together — so you can actually see the tradeoffs instead of just reading about them.

**What inputs does DocuBot take?**  
For example: user question, docs in folder, environment variables.

> A user's question (typed in `main.py` or one of the `SAMPLE_QUERIES`), the `.md`/`.txt` files in `docs/` (AUTH.md, API_REFERENCE.md, DATABASE.md, SETUP.md), and `GEMINI_API_KEY` from the environment if you want modes 1 and 3 to actually call an LLM.

**What outputs does DocuBot produce?**

> Depends on the mode: mode 1 gives a free-form LLM answer generated over the whole corpus, mode 2 gives back the raw retrieved snippets with their filenames (no generation at all), and mode 3 gives an LLM answer that's supposed to be grounded only in the retrieved snippets, or an explicit "I do not know based on the docs I have." refusal.

---

## 2. Retrieval Design

**How does your retrieval system work?**  
Describe your choices for indexing and scoring.

- How do you turn documents into an index?
- How do you score relevance for a query?
- How do you choose top snippets?

> Docs get split into small chunks: first by blank line (paragraphs), then each paragraph gets split further — into individual bullet/numbered list items if it's a flat list, and into individual sentences otherwise. Code fences are kept whole so I don't chop up a JSON example mid-line. Each chunk gets tokenized into lowercase alphanumeric words (so `auth_utils.py` becomes `["auth", "utils", "py"]` instead of one glued-together token), with stop words like "the"/"is"/"how" filtered out. `build_index` maps each meaningful word to the chunk positions it shows up in — that's the "inverted index" part.
>
> For scoring, `retrieve()` first uses the index to grab candidate chunks that share at least one meaningful word with the query (instead of scanning literally everything), then `score_document` counts how many *distinct* meaningful query words appear as whole words in that chunk — capped at 1 per word, so a chunk that repeats "token" five times doesn't outscore one that just mentions it once. Candidates are sorted by that score and I keep the top 3 (`top_k=3`), as long as they clear a floor (`min_score=1`, so a chunk has to share at least one real word — no zero-overlap chunks sneak in).

**What tradeoffs did you make?**  
For example: speed vs precision, simplicity vs accuracy.

> Definitely simplicity over accuracy. This is pure literal keyword overlap — no embeddings, no synonyms, no understanding that "refresh a token" and "how do I refresh" are the same idea if the words don't literally match. I also found out the hard way that ties matter a lot: when several chunks score the same, whichever one happens to appear earlier in the document gets picked, not whichever one is actually most relevant. I ran into this directly — see the failure cases below. Splitting into really small chunks (sentences/list items) made retrieval more *precise* when it worked, but it also means a chunk can lose the surrounding context it needs (a bullet point separated from the sentence that explains it), and it increases the chance that ties happen in the first place because more chunks end up sharing the same 1-2 word overlap.

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**  
Briefly describe how each mode behaves.

- Naive LLM mode:
- Retrieval only mode:
- RAG mode:

> - **Naive LLM mode**: always calls Gemini, and gives it the *entire* docs corpus concatenated together, no retrieval step at all. It's basically "let the model read everything and figure it out."
> - **Retrieval only mode**: never calls the LLM. It just runs `retrieve()` and prints whatever snippets came back, verbatim.
> - **RAG mode**: calls the LLM, but only hands it the top 3 snippets from `retrieve()` — not the full corpus. It's also the only mode with a guardrail check *before* the LLM call even happens (see below), so sometimes it refuses without spending an API call at all.

**What instructions do you give the LLM to keep it grounded?**  
Summarize the rules from your prompt. For example: only use snippets, say "I do not know" when needed, cite files.

> In `answer_from_snippets` (llm_client.py), the prompt tells Gemini to: only use the info in the provided snippets and not invent functions/endpoints/config values, reply with the exact phrase "I do not know based on the docs I have." if the snippets aren't enough to answer confidently, and mention which files it relied on when it does answer. The naive mode prompt (`naive_answer_over_full_docs`) doesn't have any of these rules — it's just "here's the docs, here's the question," which matters a lot for the failure cases below.

---

## 4. Experiments and Comparisons

Run the **same set of queries** in all three modes. Fill in the table with short notes.

You can reuse or adapt the queries from `dataset.py`.

| Query | Naive LLM: helpful or harmful? | Retrieval only: helpful or harmful? | RAG: helpful or harmful? | Notes |
|------|---------------------------------|--------------------------------------|---------------------------|-------|
| Where is the auth token generated? | Helpful — correctly named `generate_access_token` in `auth_utils.py`. | Helpful — top snippet is exactly the right sentence. | Helpful — short, cited, correct answer. | All three agree now. This one used to break RAG until I fixed the tokenizer (see failure cases / limitations) — `auth_utils.py` wasn't matching the word "auth" at all before that. |
| How do I connect to the database? | Helpful, and actually the *most complete* — described both SQLite and PostgreSQL options with example connection strings. | Partially helpful — accurate, but only surfaced SETUP.md chunks about `DATABASE_URL`, missed DATABASE.md's more detailed "Connection Configuration" section entirely. | Helpful but noticeably thinner than naive — only mentions SQLite fallback, doesn't mention PostgreSQL at all, because it only saw what retrieval gave it. | This is the clearest "naive wins on completeness because it can see the whole corpus" case I found. |
| Which endpoint lists all users? | Helpful — correctly says `GET /api/users`, admin-only, needs the auth header. | Technically accurate but confusing — one snippet is the real API line ("Returns a list of all users."), but another is `get_all_users()` from DATABASE.md, which is the *internal Python helper function*, not the HTTP endpoint. Nothing in the snippets actually says the literal route path. | Refused: "I do not know based on the docs I have." | RAG fails here even though naive gets it right — see failure case 1. |
| How does a client refresh an access token? | Helpful — correctly describes `POST /api/refresh`, the required header, and the JSON response shape. | Not helpful — the 3 snippets returned don't mention "refresh" at all (they're about general token requirements and a login JSON example). | Refused: "I do not know based on the docs I have." | Same root cause as above — the actual answer chunk ("4. Refresh the token when it expires by calling `/api/refresh`") tied in score with several others and lost the tie-break. See failure case 2. |

**What patterns did you notice?**  

- When does naive LLM look impressive but untrustworthy?  
- When is retrieval only clearly better?  
- When is RAG clearly better than both?

> Naive LLM looks impressive basically always, because it's a fluent model writing full sentences, and it has the unfair advantage of seeing 100% of the docs every single time. But that confidence isn't tied to whether the answer is actually a good idea — my "How do I reset a forgotten user password?" test (in the failure cases) is the clearest example: naive gave a well-formatted, confident, two-option answer, and one of those options was "delete the entire database file." It sounded helpful. It was not.
>
> Retrieval only isn't "clearly better" than the others in any of my tests, honestly — it's more like a diagnostic tool. It's useful *for me as the developer* to see exactly what got matched and why, but as an end-user-facing answer it's just a pile of quotes with no synthesis. It shines mainly when I want to debug retrieval quality, not when I want an actual answer.
>
> RAG is clearly better than both specifically when retrieval actually finds the right chunk — then you get an answer that's both readable *and* traceable to a specific file, which naive can't offer (no citations) and retrieval-only can't offer (no synthesis). The "auth token generated" query is the best example of that after my fix. But RAG is exactly as good as its retrieval step — when retrieval misses, RAG's honesty becomes a limitation instead of a feature (it says "I don't know" even when the answer was sitting one paragraph away in the docs).

---

## 5. Failure Cases and Guardrails

**Describe at least two concrete failure cases you observed.**  
For each one, say:

- What was the question?  
- What did the system do?  
- What should have happened instead?

> **Failure case 1 — "Which endpoint lists all users?"**  
> What happened: `retrieve()` returned three chunks, and by pure keyword-overlap score, `DATABASE.md`'s `- get_all_users() / Returns a list of all user records.` tied for the top spot with `API_REFERENCE.md`'s `Returns a list of all users.` — but neither snippet actually contains the literal route `GET /api/users`; that line lives in a separate chunk ("### GET /api/users") that didn't make the top 3. RAG correctly noticed the snippets it was given don't state an actual endpoint path, and refused.  
> What should have happened: RAG should have answered `GET /api/users`, same as naive did. The retrieval step is the actual point of failure here, not the LLM — it needs to either rank the literal endpoint-header chunk higher, or keep it grouped with the description that follows it instead of scoring them as two separate, equally-weighted matches.

> **Failure case 2 — "How does a client refresh an access token?"**  
> What happened: I checked the actual scores for this query, and the correct chunk (`4. Refresh the token when it expires by calling /api/refresh`) *did* score a 2, tied with about half a dozen other chunks that only mention "access token" in passing. Since `retrieve()` breaks ties by document order (whichever chunk got loaded first), three other tied chunks won the tiebreak instead, and the refresh chunk never made it into the top 3. RAG then correctly refused given what it was handed.  
> What should have happened: same as case 1 — RAG should have found `/api/refresh` and answered like naive did. This confirmed for me that "tied score = arbitrary document order" is a real, repeatable weakness, not a one-off fluke.

**When should DocuBot say "I do not know based on the docs I have"?**  
Give at least two specific situations.

> 1. When the query shares *zero* meaningful words with anything in the docs at all — e.g. "Is there any mention of payment processing in these docs?" All three modes agreed there's nothing about payments, and refusing (or, for naive, explicitly saying "no mention of X") is the correct behavior.
> 2. When retrieval does return snippets, but none of them actually cover *enough* of the question — e.g. only 1 out of 4 meaningful query words shows up, and that word is a generic one like "token" that appears in half the corpus. That's a "looks like a match, isn't really" situation, and it's exactly what my `has_sufficient_evidence` guardrail is meant to catch before an LLM call even happens.

**What guardrails did you implement?**  
Examples: refusal rules, thresholds, limits on snippets, safe defaults.

> - `has_sufficient_evidence(query, snippets)` in `docubot.py`: requires the best-scoring snippet to cover at least *half* of the query's meaningful words (rounded up), not just one. Both `answer_retrieval_only` and `answer_rag` check this before doing anything else — if it fails, they return the refusal message directly, without even calling the LLM in RAG's case. This was a deliberate change from an earlier version where `min_score` alone gated retrieval — that let a single, often generic, keyword match count as "enough evidence," which was too loose.
> - `min_score=1` inside `retrieve()` itself, so a chunk needs at least one real (non-stopword) word in common with the query before it's considered a candidate at all.
> - Whole-word matching in `score_document` (as opposed to raw substring matching) so a query word doesn't get credit for merely being a fragment of an unrelated longer word.
> - On the LLM side, the RAG prompt itself also instructs Gemini to refuse with the exact phrase "I do not know based on the docs I have." when the snippets aren't enough — so there's a second line of defense even if my code-side guardrail lets something borderline through.

---

## 6. Limitations and Future Improvements

**Current limitations**  
List at least three limitations of your DocuBot system.

1. Scoring is pure literal keyword overlap — no synonyms, no paraphrasing, no semantic similarity. If the docs say "expiration" and the user asks about "when it expires," that's a lucky word match, not understanding.
2. Ties are broken by document/chunk order, not true relevance. I confirmed this directly causes wrong answers (failure cases 1 and 2 above) — the right chunk can score identically to several wrong ones and simply lose the coin flip.
3. Small chunks help precision but can separate an answer from the context it needs — e.g. a bullet point separated from the line above it that explains what it means. Better granularity for retrieval also means retrieval has to do more of the "which of these 6 equally-scored candidates actually matters" work, which it currently isn't equipped to do.
4. Naive mode has no code-level guardrail at all — its only grounding comes from prompt instructions, and (as shown in the failure cases) it will confidently propose things that are technically "supported" by some paragraph in the docs but are the wrong tool for the question (like suggesting a full database wipe for a password reset).

**Future improvements**  
List two or three changes that would most improve reliability or usefully.

1. Better tie-breaking — instead of falling back to document order, break ties using something like query-word coverage ratio (how many of the query's words matched relative to the query's total length) or prefer shorter, denser chunks over longer ones that happen to share the same raw count.
2. Move toward embedding-based similarity (even a simple one) instead of literal word overlap, specifically to catch paraphrased questions that don't share exact vocabulary with the docs.
3. Add a guardrail on the naive mode too — even a lightweight "does this answer's key claims actually appear in the source docs" check — since right now it's the least protected of the three modes despite being the most confident-sounding.

---

## 7. Responsible Use

**Where could this system cause real world harm if used carelessly?**  
Think about wrong answers, missing information, or over trusting the LLM.

> The clearest example I found myself: I asked naive mode "How do I reset a forgotten user password?" and it confidently offered `rm app.db` (deleting the entire local database file) as "Option 1," reasoning from the unrelated "Resetting the Environment" section of SETUP.md. If a developer trusted that without reading closely, and ran it against something that wasn't just a disposable local SQLite file, that's real data loss for a completely unrelated problem. More generally: naive mode's confidence has nothing to do with correctness, since it's not required to say "I don't know" the way RAG is — it will always produce something.

**What instructions would you give real developers who want to use DocuBot safely?**  
Write 2 to 4 short bullet points.

- Don't trust naive mode for anything operationally risky (deleting data, changing config, running commands) without independently checking the actual docs yourself — it's the mode with no refusal guardrail and the most reason to sound confident regardless.
- Treat retrieval-only output as raw material to read yourself, not as a finished answer — it's accurate but doesn't synthesize or tell you if it's actually complete.
- If RAG refuses ("I do not know based on the docs I have."), don't automatically re-ask naive mode assuming it must know better — first check whether the docs genuinely don't cover it, since sometimes (as I found) the answer was in the docs and retrieval just didn't surface the right chunk.
- When retrieval and generation disagree in confidence level, that mismatch itself is useful information — it's often a sign retrieval missed something, worth checking manually rather than just picking whichever answer sounds best.

---
