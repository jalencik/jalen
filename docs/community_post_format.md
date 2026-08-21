# The AI Engineering & Machine Learning channel post format

This is the mould for posts in his Telegram channel. It was reverse-engineered
from a real post of his (the Lee Language Lab / NLP opportunity announcement),
so it is his actual house style, not a generic template.

Follow the STRUCTURE exactly. Vary the words.

---

## The skeleton

```
<b>{Title}</b> {one emoji}

{Opening paragraph: two or three sentences. Names the opportunity, the
organisation, and the person leading it. Every proper noun that matters —
lab name, professor, university, field — is <b>bold</b>. Plain sentences,
no hype.}

{One short line stating the focus, with the field <b>bold</b>.}

<b>{Section heading}:</b> {emoji}

• {item}
• {item}
• {item}
• {item}

<b>Requirements:</b> {emoji}

• {item}
• {item}
• Leave a reaction to this post

<blockquote expandable><b>Most Asked Questions</b>

<b>1</b> {Question?}
— {answer line}
— {answer line}

<b>2</b> {Question?}
{Short lead-in sentence:}
— {answer line}
— {answer line}

<b>3</b> {Question?}
{Prose answer, two or three sentences, with key terms bold.}
</blockquote>

Interested? Don't hesitate to dm me 👇:
@Iht_student
```

## Rules that matter

**Bold carries the information.** Names, institutions, fields, numbers and
deadlines are bold. Nothing else is. Bolding a whole sentence defeats it.

**Bullets are `•`, never `-` or `*`.** In the Q&A block, sub-items use an em
dash `—` instead, which is what visually separates a question's answers from
the post's own bullet list.

**The Q&A goes in an expandable blockquote.** `<blockquote expandable>` is a
real Telegram feature: the post shows a collapsed "Q&A" strip that the reader
taps to open. It is what keeps a long, genuinely useful post from looking
like a wall of text in the channel feed. Without `expandable` it renders as
one enormous permanently-open quote.

**Questions are numbered with bold digits**, not "1." — `<b>1</b> What will
you get?`

**The last two lines never change:**

```
Interested? Don't hesitate to dm me 👇:
@Iht_student
```

**Emoji are punctuation, not decoration.** One after the title, one after each
section heading, one in the sign-off. Never inside a sentence, never two in a
row.

**Length.** The visible part — everything above the Q&A block — fits on one
phone screen. Anything longer belongs inside the expandable block.

## Telegram HTML: what is actually supported

Only these tags. Anything else is rejected by the API and the post fails to
save, which is worse than looking plain:

`<b>` `<i>` `<u>` `<s>` `<code>` `<pre>` `<a href="">` `<blockquote>`
`<blockquote expandable>` `<tg-spoiler>`

No `<h1>`, no `<ul>`, no `<li>`, no `<br>`, no `<p>`, no markdown `**`.
Line breaks are literal newlines. Ampersands and angle brackets in the text
itself must be escaped as `&amp;` `&lt;` `&gt;`.

## Worked example (his real post, abbreviated)

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
Students who are passionate about:
— Natural Language Processing
— Machine Learning / AI
— Multilingual &amp; multicultural NLP
— Low-resource languages

Applicants should also be prepared to complete the required challenge tasks
and demonstrate strong academic readiness.

<b>3</b> How are applicants selected?
Applicants must first complete the required forms and <b>Challenge Tasks 1, 2,
and 3</b>. Their solutions will be graded by the lab. Applicants who meet the
lab's required <b>bar of excellence</b> may then be invited to an interview.
</blockquote>

Interested? Don't hesitate to dm me 👇:
@Iht_student
```
