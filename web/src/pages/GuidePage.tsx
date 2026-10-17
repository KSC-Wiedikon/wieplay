import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { Card } from '../components/Card'
import { CheckIcon, ChevronRightIcon, CloseIcon } from '../components/icons'
import { PageHeader } from '../components/PageHeader'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { guideDe } from '../i18n/guide.de'
import { guideEn, type Guide, type GuideKey } from '../i18n/guide.en'
import { useIsGuest } from '../lib/guests'
import { NotFoundPage } from './NotFoundPage'

/** The guide for team helpers (PLAN §5.1 "analyst"): reviewing suggestions, tagging, why it matters. */
export function GuidePage() {
  // KSCW's helpers' guide: guest clubs have no helpers.
  if (useIsGuest()) return <NotFoundPage />
  return <GuideContent />
}

function GuideContent() {
  const { t, i18n } = useTranslation()
  const g: Guide = i18n.language === 'de' ? guideDe : guideEn
  const sections: [string, string][] = [
    ['why', g.why.title],
    ['start', g.start.title],
    ['review', g.review.title],
    ['done', g.done.title],
    ['tag', g.tag.title],
    ['actions', g.actions.title],
    ['rights', g.rights.title],
  ]

  return (
    <div className="flex flex-col gap-8 sm:gap-10">
      <title>{`${g.title} · WiePlay`}</title>
      <PageHeader
        eyebrow={
          <>
            <Link to="/" className="link">
              {t('home.title')}
            </Link>
            <ChevronRightIcon className="size-4" />
            <span>{g.title}</span>
          </>
        }
        title={g.title}
        subtitle={<p className="max-w-3xl">{g.subtitle}</p>}
      />

      <div className="grid gap-8 lg:grid-cols-[minmax(0,48rem)_15rem] lg:items-start xl:gap-12">
        <nav aria-label={g.onThisPage} className="lg:sticky lg:top-6 lg:order-last">
          <Card className="flex flex-col gap-2 p-4">
            <h2 className="text-sm font-semibold text-muted">{g.onThisPage}</h2>
            <ol className="flex flex-col gap-1 text-sm">
              {sections.map(([id, title], index) => (
                <li key={id} className="flex gap-2">
                  <span className="w-4 shrink-0 text-muted tabular-nums">{index + 1}</span>
                  <a href={`#${id}`} className="link">
                    {title}
                  </a>
                </li>
              ))}
            </ol>
          </Card>
        </nav>

        <article className="flex min-w-0 flex-col gap-10">
          <GuideSection id="why" title={g.why.title}>
            {g.why.body.map((text) => (
              <p key={text}>{text}</p>
            ))}
          </GuideSection>

          <GuideSection id="start" title={g.start.title}>
            <Steps steps={g.start.steps} />
          </GuideSection>

          <GuideSection id="review" title={g.review.title}>
            <p>{g.review.intro}</p>
            <Steps steps={g.review.steps} />
            <KeyTable title={g.review.keysTitle} rows={g.review.keys} guide={g} />
            <Tips tips={g.review.tips} />
          </GuideSection>

          <GuideSection id="done" title={g.done.title}>
            {g.done.body.map((text) => (
              <p key={text}>{text}</p>
            ))}
          </GuideSection>

          <GuideSection id="tag" title={g.tag.title}>
            <p>{g.tag.intro}</p>
            <Card className="flex flex-col gap-3 p-4">
              <h3 className="text-sm font-semibold text-muted">{g.tag.grammarTitle}</h3>
              <p className="font-mono text-base font-bold">{g.tag.grammar}</p>
              <ul className="flex flex-col gap-1.5">
                {g.tag.examples.map((example) => (
                  <li key={example.entry} className="flex flex-wrap items-baseline gap-x-3">
                    <Kbd className="text-sm">{example.entry}</Kbd>
                    <span>{example.text}</span>
                  </li>
                ))}
              </ul>
            </Card>
            <div className="grid gap-4 sm:grid-cols-2">
              <Card className="overflow-hidden">
                <Table caption={g.tag.skillsTitle}>
                  <THead>
                    <tr>
                      <Th>{g.key}</Th>
                      <Th>{g.tag.skillsTitle}</Th>
                    </tr>
                  </THead>
                  <TBody>
                    {g.tag.skills.map((skill) => (
                      <tr key={skill.code}>
                        <Td className="w-16">
                          <Kbd>{skill.code}</Kbd>
                        </Td>
                        <Td>{skill.name}</Td>
                      </tr>
                    ))}
                  </TBody>
                </Table>
              </Card>
              <Card className="overflow-hidden">
                <Table caption={g.tag.gradesTitle}>
                  <THead>
                    <tr>
                      <Th>{g.key}</Th>
                      <Th>{g.tag.gradesTitle}</Th>
                    </tr>
                  </THead>
                  <TBody>
                    {g.tag.grades.map((grade) => (
                      <tr key={grade.grade}>
                        <Td className="w-16">
                          <Kbd>{grade.grade}</Kbd>
                        </Td>
                        <Td>{grade.text}</Td>
                      </tr>
                    ))}
                  </TBody>
                </Table>
              </Card>
            </div>
            <KeyTable title={g.tag.keysTitle} rows={g.tag.keys} guide={g} />
            <Tips tips={g.tag.tips} />
          </GuideSection>

          <GuideSection id="actions" title={g.actions.title}>
            <p>{g.actions.body}</p>
            <KeyTable rows={g.actions.keys} guide={g} label={g.actions.title} />
          </GuideSection>

          <GuideSection id="rights" title={g.rights.title}>
            <div className="grid gap-4 sm:grid-cols-2">
              <Card className="flex flex-col gap-2 p-4">
                <h3 className="font-semibold">{g.rights.canTitle}</h3>
                <ul className="flex flex-col gap-1.5">
                  {g.rights.can.map((text) => (
                    <li key={text} className="flex items-start gap-2">
                      <CheckIcon className="mt-0.5 size-4 text-link" />
                      <span>{text}</span>
                    </li>
                  ))}
                </ul>
              </Card>
              <Card className="flex flex-col gap-2 p-4">
                <h3 className="font-semibold">{g.rights.cannotTitle}</h3>
                <ul className="flex flex-col gap-1.5">
                  {g.rights.cannot.map((text) => (
                    <li key={text} className="flex items-start gap-2">
                      <CloseIcon className="mt-0.5 size-4 text-danger" />
                      <span>{text}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
            <p className="text-muted">{g.rights.privacy}</p>
          </GuideSection>
        </article>
      </div>
    </div>
  )
}

function GuideSection({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="flex scroll-mt-6 flex-col gap-4 leading-relaxed">
      <h2 id={`${id}-title`} className="text-xl font-bold tracking-tight">
        {title}
      </h2>
      {children}
    </section>
  )
}

function Steps({ steps }: { steps: string[] }) {
  return (
    <ol className="flex flex-col gap-3">
      {steps.map((step, index) => (
        <li key={step} className="flex items-start gap-3">
          <span className="grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-sm font-bold text-on-accent-soft tabular-nums">
            {index + 1}
          </span>
          <span className="pt-0.5">{step}</span>
        </li>
      ))}
    </ol>
  )
}

function Tips({ tips }: { tips: string[] }) {
  return (
    <ul className="flex flex-col gap-2 rounded-card border border-hairline bg-sunken p-4 text-sm">
      {tips.map((tip) => (
        <li key={tip} className="flex items-start gap-2">
          <span aria-hidden="true" className="mt-2 size-1.5 shrink-0 rounded-full bg-muted" />
          <span>{tip}</span>
        </li>
      ))}
    </ul>
  )
}

function Kbd({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={`rounded-md border border-hairline-strong bg-sunken px-1.5 py-0.5 font-mono text-xs font-semibold whitespace-nowrap ${className}`}
    >
      {children}
    </kbd>
  )
}

function KeyTable({ title, label, rows, guide }: { title?: string; label?: string; rows: GuideKey[]; guide: Guide }) {
  return (
    <div className="flex flex-col gap-2">
      {title && <h3 className="font-semibold">{title}</h3>}
      <Card className="overflow-hidden">
        <Table caption={title ?? label}>
          <THead>
            <tr>
              <Th>{guide.key}</Th>
              <Th>{guide.meaning}</Th>
            </tr>
          </THead>
          <TBody>
            {rows.map((row) => (
              <tr key={row.text}>
                <Td className="w-32 sm:w-56">
                  <span className="flex flex-wrap gap-1">
                    {row.keys.map((key) => (
                      <Kbd key={key}>{key}</Kbd>
                    ))}
                  </span>
                </Td>
                <Td>{row.text}</Td>
              </tr>
            ))}
          </TBody>
        </Table>
      </Card>
    </div>
  )
}
