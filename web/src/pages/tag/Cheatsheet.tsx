import { useTranslation } from 'react-i18next'
import { Modal } from '../../components/Modal'
import { gradeLabel, skillLabel } from '../../lib/labels'
import { useUi, useWorkspace } from './context'

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

/** The `?` overlay: every key, the entry grammar and what each grade means per skill. */
export function Cheatsheet() {
  const { t } = useTranslation()
  const { controller, taxonomy } = useWorkspace()
  const ui = useUi()

  const groups: { title: string; rows: [string[], string][] }[] = [
    {
      title: t('tagging.keys.groups.playback'),
      rows: [
        [['Space'], t('tagging.keys.space')],
        [['←', '→'], t('tagging.keys.arrows')],
        [['Shift + ←', 'Shift + →'], t('tagging.keys.shiftArrows')],
        [['Ctrl + ←', 'Ctrl + →'], t('tagging.keys.ctrlArrows')],
        [[',', '.'], t('tagging.keys.speed')],
        [['Shift + D'], t('tagging.keys.skip')],
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
    {
      title: t('tagging.keys.groups.rallies'),
      rows: [
        [['R'], t('tagging.keys.r')],
        [['U', 'T', 'Enter', 'Esc'], t('tagging.keys.winner')],
        [['T'], t('tagging.keys.t')],
        [['B'], t('tagging.keys.b')],
      ],
    },
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
        </div>
        <div className="flex flex-col gap-5">
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-semibold text-muted">{t('tagging.keys.grammarTitle')}</h3>
            <p className="font-mono text-sm font-bold">{t('tagging.keys.grammar')}</p>
            <ul className="flex flex-col gap-1 text-sm">
              {(
                [
                  ['7A#', 'kill'],
                  ['a12S=', 'serveError'],
                  ['5R+', 'reception'],
                  ['3A#46', 'zones'],
                ] as const
              ).map(([example, key]) => (
                <li key={example}>
                  <span className="font-mono font-bold">{example}</span> {t(`tagging.keys.examples.${key}`)}
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
                      {taxonomy.grades.map((g) => (
                        <td key={g} className="px-1 py-1 align-top">
                          {gradeLabel(t, s, g)}
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
