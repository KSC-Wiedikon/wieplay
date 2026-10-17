import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { Modal } from '../../components/Modal'
import { gradeLabel, sideLabel, skillLabel } from '../../lib/labels'
import { useSideNames } from '../../lib/sides'
import { useActionReview, useReview, useUi, useWorkspace } from './context'
import { GuideLink } from './GuideLink'

function Keys({ keys }: { keys: string[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {keys.map((k) => (
        <kbd key={k} className="rounded-md border border-hairline bg-sunken px-1.5 py-0.5 font-mono text-xs font-semibold whitespace-nowrap">
          {k}
        </kbd>
      ))}
    </span>
  )
}

/** Entry examples per sport: the entry and what it means. */
function examplesFor(t: TFunction, basketball: boolean): [string, string][] {
  if (basketball) {
    return [
      ['23S3+', t('tagging.basketball.keys.examples.three')],
      ['a4S2-', t('tagging.basketball.keys.examples.two')],
      ['11RO', t('tagging.basketball.keys.examples.rebound')],
      ['5FT+', t('tagging.basketball.keys.examples.freeThrow')],
    ]
  }
  return (
    [
      ['7A#', 'kill'],
      ['a12S=', 'serveError'],
      ['5R+', 'reception'],
      ['3A#46', 'zones'],
    ] as const
  ).map(([example, key]) => [example, t(`tagging.keys.examples.${key}`)])
}

/**
 * The `?` overlay: every key, the entry grammar and what each grade means per skill. Basketball
 * has no rally keys and its own grammar and codes.
 */
export function Cheatsheet() {
  const { t } = useTranslation()
  const { controller, taxonomy } = useWorkspace()
  const ui = useUi()
  const reviewing = useReview().active
  const reviewingActions = useActionReview().active && reviewing
  const basketball = taxonomy.sport === 'basketball'
  const examples = examplesFor(t, basketball)
  const names = useSideNames()
  const teams = { us: sideLabel(t, 'us', names), them: sideLabel(t, 'them', names) }

  const all: { title: string; rows: [string[], string][] }[] = [
    {
      title: t('tagging.keys.groups.playback'),
      rows: [
        [['Space'], t('tagging.keys.space')],
        [['←', '→'], t('tagging.keys.arrows')],
        [['Shift + ←', 'Shift + →'], t('tagging.keys.shiftArrows')],
        ...(basketball ? [] : [[['Ctrl + ←', 'Ctrl + →'], t('tagging.keys.ctrlArrows')] as [string[], string]]),
        [[',', '.'], t('tagging.keys.speed')],
        ...(basketball ? [] : [[['Shift + D'], t('tagging.keys.skip')] as [string[], string]]),
      ],
    },
    {
      title: t('tagging.keys.groups.tagging'),
      rows: [
        [['0–9', 'a'], t('tagging.keys.start')],
        [['Enter'], t('tagging.keys.enter')],
        [['Tab'], t('tagging.keys.tab')],
        [['Backspace'], t('tagging.keys.backspace')],
        [['Esc'], t('tagging.keys.esc')],
        [['Ctrl + Z', 'Ctrl + Shift + Z'], t('tagging.keys.undo')],
      ],
    },
    basketball
      ? {
          title: t('tagging.basketball.keys.breaks'),
          rows: [
            [['T'], t('tagging.keys.t')],
            [['B'], t('tagging.basketball.keys.b')],
          ],
        }
      : {
          title: t('tagging.keys.groups.rallies'),
          rows: [
            [['R'], t('tagging.keys.r')],
            [['U', 'T', 'Enter', 'Esc'], t('tagging.keys.winner', teams)],
            [['T'], t('tagging.keys.t')],
            [['B'], t('tagging.keys.b')],
          ],
        },
    // Reviewing WiePlay's suggestions (volleyball: basketball has no rallies to suggest).
    ...(basketball
      ? []
      : [
          {
            title: t('tagging.keys.groups.review'),
            rows: [
              [['Enter'], t('tagging.keys.review.accept')],
              [['X'], t('tagging.keys.review.reject')],
              [['←', '→'], t('tagging.keys.review.start')],
              [['Shift + ←', 'Shift + →'], t('tagging.keys.review.end')],
              [['Alt'], t('tagging.keys.review.frame')],
              [['1', '2'], t('tagging.keys.review.serve', teams)],
              [['3', '4'], t('tagging.keys.review.won', teams)],
              [['W', 'B', 'T', 'D', 'O'], t('tagging.keys.review.label')],
              [['M'], t('tagging.keys.review.merge')],
              [['S'], t('tagging.keys.review.split')],
              [['U'], t('tagging.keys.review.undo')],
              [['↑', '↓'], t('tagging.keys.review.move')],
              [['Space'], t('tagging.keys.review.space')],
              [['Esc'], t('tagging.keys.review.leave')],
            ] as [string[], string][],
          },
          {
            title: t('actions.keys.group'),
            rows: [
              [['Enter'], t('actions.keys.accept')],
              [['X'], t('actions.keys.reject')],
              [['S', 'R', 'E', 'A', 'B', 'D', 'F'], t('actions.keys.skill')],
              [['#', '+', '!', '-', '/', '='], t('actions.keys.grade')],
              [['0–9', 'Enter'], t('actions.keys.number')],
              [['Tab'], t('actions.keys.side')],
              [['←', '→'], t('actions.keys.nudge')],
              [['Shift + Enter'], t('actions.keys.rally')],
              [['U'], t('actions.keys.undo')],
              [['↑', '↓'], t('actions.keys.move')],
              [['Space'], t('actions.keys.space')],
              [['Esc'], t('actions.keys.leave')],
            ] as [string[], string][],
          },
        ]),
    {
      title: t('tagging.keys.groups.selected'),
      rows: [
        [['↑', '↓'], t('tagging.keys.select')],
        [['Enter'], t('tagging.keys.edit')],
        [['Delete'], t('tagging.keys.del')],
        [['G'], t('tagging.keys.g')],
        [['F'], t('tagging.keys.f')],
        [['[', ']', 'C'], t('tagging.keys.clip')],
        [['Shift + C'], t('tagging.keys.clipWindow')],
        [['?'], t('tagging.keys.help')],
      ],
    },
  ]

  // While reviewing, its keys come first (the actions queue's when that is on).
  const review = reviewingActions ? t('actions.keys.group') : t('tagging.keys.groups.review')
  const groups = reviewing ? [...all.filter((g) => g.title === review), ...all.filter((g) => g.title !== review)] : all

  return (
    <Modal open={ui.cheatsheet} title={t('tagging.keys.title')} onClose={() => controller.toggleCheatsheet(false)} size="xl">
      <div className="grid gap-6 md:grid-cols-2">
        <div className="flex flex-col gap-5">
          {groups.map((group) => (
            <section key={group.title} className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold text-muted">{group.title}</h3>
              <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 gap-y-1.5 text-sm">
                {group.rows.map(([keys, text]) => (
                  <div key={text} className="contents">
                    <dt>
                      <Keys keys={keys} />
                    </dt>
                    <dd>{text}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
          <p className="text-xs text-muted">{t('tagging.keys.note')}</p>
          <GuideLink />
        </div>
        <div className="flex flex-col gap-5">
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-muted">{t('tagging.keys.grammarTitle')}</h3>
            <p className="font-mono text-sm font-bold">{basketball ? t('tagging.basketball.keys.grammar') : t('tagging.keys.grammar')}</p>
            <ul className="flex flex-col gap-1 text-sm">
              {examples.map(([example, meaning]) => (
                <li key={example}>
                  <span className="font-mono font-bold">{example}</span> {meaning}
                </li>
              ))}
            </ul>
          </section>
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-muted">{t('tagging.keys.gradesTitle')}</h3>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-xs">
                <thead>
                  <tr>
                    <th scope="col" className="border-b border-hairline py-1 pr-2 font-semibold">
                      {t('tagging.filters.skill')}
                    </th>
                    {taxonomy.grades.map((g) => (
                      <th key={g} scope="col" className="border-b border-hairline px-1 py-1 font-mono font-bold">
                        {g}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {taxonomy.skills.map((s) => (
                    <tr key={s.code} className="border-b border-hairline">
                      <th scope="row" className="py-1 pr-2 font-semibold whitespace-nowrap">
                        <span className="font-mono">{s.code}</span> {skillLabel(t, s)}
                      </th>
                      {/* A skill without grades (basketball: all but shots) is entered as it is. */}
                      {taxonomy.grades.map((g) => (
                        <td key={g} className="px-1 py-1 align-top">
                          {s.graded ? gradeLabel(t, s, g) : '–'}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      </div>
    </Modal>
  )
}
