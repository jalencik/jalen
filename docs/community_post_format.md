# The AI Engineering & Machine Learning channel post format

The house style for his Telegram channel, reverse-engineered from his real
posts. Follow the STRUCTURE exactly. Vary the words.

---

## First: which of the two templates is this?

He was explicit that these are different posts, not one post with a renamed
section. Pick before writing:

| The opportunity is… | Use template | Middle section is |
|---|---|---|
| a **lab** — a named research group, a professor, ongoing work | **LAB** | `Research Areas:` |
| a **project, research programme, mentorship or partnership** | **OPPORTUNITY** | `Project:` |

If the email genuinely does not say which, say so and ask him rather than
guessing. A lab post that describes a one-off project reads as sloppy to
exactly the people he is trying to attract.

**In both templates `Requirements:` is its own separate section.** It is never
folded into the section above it.

---

## Template A — LAB

```
<b>{Title}</b> 🖊

We're excited to announce a new research opportunity with the <b>{Lab
Name}</b>, led by <b>{Prof. Name}</b>, {their role} at {institution}.
The lab focuses on <b>{field}</b>

<b>Research Areas:</b> 🛣

• {area}
• {area}
• {area}
• {area}
• Research toward publication

<b>Requirements:</b> 👏

• Passion in {field}
• Devotion of {N}-{M} hr/wk
• Leave a reaction to this post

{Q&A BLOCK — see below}

Interested? Don't hesitate to dm me 👇:
@Iht_student
```

## Template B — PROJECT / RESEARCH / MENTORSHIP

```
<b>{Title}</b> 🚀

{One or two sentences: what it is, who runs it, and what it is for. Every
proper noun that matters is <b>bold</b>.}

<b>Project:</b> 📌

• {what the work actually is}
• {what gets built or produced}
• {timeline or scale}
• {outcome — publication, demo, deployment}

<b>Requirements:</b> 👏

• {skill or background}
• Devotion of {N}-{M} hr/wk
• Leave a reaction to this post

{Q&A BLOCK — see below}

Interested? Don't hesitate to dm me 👇:
@Iht_student
```

---

## The Q&A block — THREE questions, always these three

Identical in both templates. Not two, not four, and not different questions:
these are the three every applicant asks, in this order.

```
<blockquote expandable><b>Most Asked Questions</b>

<b>1</b> What will you get?
— {benefit}
— {benefit}
— {benefit}
— {benefit}

<b>2</b> Who should apply?
Students who are competent in:
— {area}
— {area}
— {area}
— {area}

{One closing sentence about readiness or required tasks.}

<b>3</b> How are applicants selected?
{How the process works — forms, tasks, grading, interview. Bold the concrete
requirements: <b>Challenge Tasks 1, 2, and 3</b>, <b>bar of excellence</b>.}

Before interviews, {they} will also review:
— GPA
— Resume
— University background
— Academic readiness
— Research alignment
</blockquote>
```

Question 2 opens with the exact line **`Students who are competent in:`**
followed by em-dash items. That phrasing is his.

---

## Rules that matter

**Bold carries the information.** Names, institutions, fields, numbers and
deadlines are bold. Nothing else. Bolding a whole sentence defeats it.

**Bullets are `•`.** Never `-` or `*`. Inside the Q&A block, sub-items use an
em dash `—` instead, which is what separates a question's answers from the
post's own bullet list.

**The Q&A goes in an expandable blockquote.** `<blockquote expandable>` is a
real Telegram feature: the post shows a collapsed "Q&A" strip the reader taps
to open. Without `expandable` it renders permanently open and the post becomes
a wall of text in the channel feed.

**Questions are numbered with bold digits** — `<b>1</b> What will you get?`,
not "1." and not "Question 1:".

**The last two lines never change:**

```
Interested? Don't hesitate to dm me 👇:
@Iht_student
```

**Emoji are punctuation, not decoration.** One after the title, one after each
section heading, one in the sign-off. Never inside a sentence, never two in a
row. The set he uses:

| Where | Emoji |
|---|---|
| Lab title | 🖊 |
| Project/programme title | 🚀 |
| `Research Areas:` | 🛣 |
| `Project:` | 📌 |
| `Requirements:` | 👏 |
| Q&A heading | ❓ |
| Sign-off | 👇 |

**Length.** Everything above the Q&A block fits on one phone screen. Anything
longer belongs inside the expandable block.

---

## Premium emoji and stickers

He has Telegram Premium and wants his own premium emoji in the posts. (A
sticker is a different matter: see the last paragraph of this section.) Write
the post exactly as above first, with the ordinary emoji from the table. Then swap each one for its premium version in the
same place: one for one, never an extra emoji, never in a new position. The
layout does not change; only what is drawn there does.

A premium emoji is a tag around the ordinary emoji it replaces:

```
<tg-emoji emoji-id="ID">🚀</tg-emoji>
```

The emoji between the tags is the fallback: the same emoji that was there
before, which is what anyone without Premium sees. It stays, it is exactly one
emoji, and it is never a word, a number or a different emoji. An empty tag is
refused, and so is one around a word.

**The id is the part you cannot know.** Never write one from memory and never
guess one: a made-up id is refused at the send, and a real id from a different
emoji is a wrong picture in his channel. Call `find_premium_emoji` once, with
every emoji the post uses as the query (`🖊 🚀 🛣 📌 👏 ❓ 👇` is the whole house
set) and `learn_from` set to the channel's exact title. The ones he already
uses in his recent posts come first, so a new post matches the old ones. Paste
each tag it returns where the ordinary emoji was. An emoji it finds nothing for
stays ordinary, and you say which.

After the send, the reply says how many premium emoji arrived. Telegram drops
them without an error when an account is not Premium, so a post can be "sent"
and plain. Pass that sentence on as it is.

**A sticker only when he asks for one.** No sticker follows a post on your own:
send one only when he asked for that post to have it ("post it with a sticker"),
because a sticker that reached the channel cannot be taken back. It is never
part of the post and never inside it: it is its own message. When he asked, send
the post first, then `send_sticker` to the same chat, with the emoji that suits
the post. With no pack named it looks only in his favourites; if none has the
emoji it sends nothing and lists the packs that do, and you ask him which pack
to use. If there is no matching sticker, say so and send nothing; never send a
random one.

---

## Telegram HTML: what is actually supported

Only these tags, and every one of them arrives as formatting:

`<b>` `<i>` `<u>` `<s>` `<code>` `<pre>` `<a href="https://...">` `<blockquote>`
`<blockquote expandable>` `<tg-spoiler>` `<tg-emoji emoji-id="ID">`

(`<strong>` `<em>` `<ins>` `<strike>` `<del>` and `<span class="tg-spoiler">`
are other spellings of the same things. `<a>` takes `href` and nothing else.
`<tg-emoji>` takes `emoji-id` and nothing else, and wraps one emoji.)

Anything else - `<h1>`, `<ul>`, `<li>`, `<br>`, `<p>`, a tag never closed, a
tag closed out of order - and the post fails to save: nothing is sent or
saved, and the reply names the tag. It is not guessed at, because a guess
either eats words or posts the tags as visible text. No markdown `**` either:
it arrives as two asterisks.

Line breaks are literal newlines. Write `&amp;` `&lt;` `&gt;` for those
characters in the text itself; `&lt;b&gt;` is how to show a tag as text. A
bare `&` - a link's `?a=1&b=2` - is kept exactly as written.

---

## Worked example — his real Lee Language Lab post (Template A)

```
<b>Lab Opportunity for NLP students</b> 🖊

We're excited to announce a new research opportunity with the <b>Lee Language
Lab (L³)</b>, led by <b>Prof. Annie En-Shiun Lee</b>, a Computer Science
faculty member at Ontario Tech University and status-only faculty member at
the University of Toronto.
The lab focuses on <b>multilingual and multicultural Natural Language
Processing (NLP)</b>

<b>Research Areas:</b> 🛣

• Multilingual datasets &amp; linguistic diversity analytics
• Efficient model adaptation for low-resource languages
• Real-world NLP applications
• Education, clinical text, games &amp; more
• Research toward publication

<b>Requirements:</b> 👏

• Passion in NLP
• Devotion of 5-10 hr/wk
• Leave a reaction to this post

<blockquote expandable><b>Most Asked Questions</b>

<b>1</b> What will you get?
— Real Research Experience
— Research &amp; Publication Training
— Potential Research Contributions
— Opportunity to work toward publication

<b>2</b> Who should apply?
Students who are competent in:
— Natural Language Processing
— Machine Learning / AI
— Multilingual &amp; multicultural NLP
— Low-resource languages
— Data analysis &amp; research
— Language and cultural diversity
— Independent research work

Applicants should also be prepared to complete the required challenge tasks
and demonstrate strong academic readiness.

<b>3</b> How are applicants selected?
Applicants must first complete the required forms and <b>Challenge Tasks 1, 2,
and 3</b>. Their solutions will be graded by the lab. Applicants who meet the
lab's required <b>bar of excellence</b> may then be invited to an interview.

Before interviews, the lab will also review:
— GPA
— Resume
— University background
— Academic readiness
— Research alignment
</blockquote>

Interested? Don't hesitate to dm me 👇:
@Iht_student
```
