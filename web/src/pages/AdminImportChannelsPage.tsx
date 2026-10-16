import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { AdminShell } from '../components/AdminShell'
import { Button } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Field, TextInput } from '../components/Field'
import { LoadError, Message } from '../components/Message'
import { PageSpinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead } from '../components/Table'
import { ApiError, ForbiddenError } from '../lib/api'
import { formatDate } from '../lib/format'
import { useAddImportChannel, useImportChannels, useRemoveImportChannel } from '../lib/queries'
import { useToast } from '../lib/toast'
import type { ImportChannel } from '../lib/types'
import { channelDisplay, platformName } from '../lib/video'

/** The allow-list for imports from YouTube and Twitch links (PLAN §7.2.2). */
export function AdminImportChannelsPage() {
  const { t } = useTranslation()
  return (
    <AdminShell title={t('admin.importChannels.title')}>
      <div className="flex flex-col gap-8">
        <ChannelList />
        <AddChannel />
      </div>
    </AdminShell>
  )
}

/** "@kscwiedikon", linking to the channel's page. */
function ChannelRef({ channel }: { channel: ImportChannel }) {
  const { text, url } = channelDisplay(channel)
  if (!url) return <span className="font-mono">{text}</span>
  return (
    <a href={url} target="_blank" rel="noreferrer noopener" className="font-mono text-link underline-offset-2 hover:underline">
      {text}
    </a>
  )
}

function ChannelList() {
  const { t } = useTranslation()
  const id = useId()
  const channels = useImportChannels()
  const remove = useRemoveImportChannel()
  const [removing, setRemoving] = useState<ImportChannel | null>(null)

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <h2 id={id} className="text-lg font-semibold">
          {t('admin.importChannels.title')}
        </h2>
        <p className="text-sm text-muted">{t('admin.importChannels.hint')}</p>
      </div>
      {channels.isPending ? (
        <PageSpinner />
      ) : channels.isError ? (
        channels.error instanceof ForbiddenError ? (
          <Message>{t('admin.forbidden')}</Message>
        ) : (
          <LoadError onRetry={() => void channels.refetch()} />
        )
      ) : channels.data.length === 0 ? (
        <Message>{t('admin.importChannels.empty')}</Message>
      ) : (
        <Card className="overflow-hidden">
          <Table caption={t('admin.importChannels.title')}>
            <THead>
              <tr>
                <Th>{t('admin.importChannels.label')}</Th>
                <Th className="hidden sm:table-cell">{t('admin.importChannels.platform')}</Th>
                <Th className="hidden sm:table-cell">{t('admin.importChannels.channel')}</Th>
                <Th className="hidden sm:table-cell">{t('admin.importChannels.added')}</Th>
                <Th>
                  <span className="sr-only">{t('admin.importChannels.remove')}</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {channels.data.map((channel) => (
                <tr key={channel.id}>
                  <Td>
                    <span className="block font-semibold">{channel.label}</span>
                    <span className="mt-1 block text-xs text-muted sm:hidden">
                      {platformName(channel.platform)} · <ChannelRef channel={channel} />
                    </span>
                  </Td>
                  <Td className="hidden sm:table-cell">{platformName(channel.platform)}</Td>
                  <Td className="hidden sm:table-cell">
                    <ChannelRef channel={channel} />
                  </Td>
                  <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">{formatDate(channel.added_at)}</Td>
                  <Td tight className="text-right">
                    <Button
                      variant="ghostDanger"
                      className="px-3"
                      aria-label={t('admin.importChannels.removeFor', { label: channel.label })}
                      onClick={() => setRemoving(channel)}
                    >
                      {t('admin.importChannels.remove')}
                    </Button>
                  </Td>
                </tr>
              ))}
            </TBody>
          </Table>
        </Card>
      )}
      <ConfirmDialog
        open={removing !== null}
        title={t('admin.importChannels.removeTitle', { label: removing?.label ?? '' })}
        confirmLabel={t('admin.importChannels.remove')}
        danger
        busy={remove.isPending}
        onCancel={() => setRemoving(null)}
        onConfirm={() => {
          if (removing) remove.mutate(removing, { onSettled: () => setRemoving(null) })
        }}
      >
        {t('admin.importChannels.removeBody')}
      </ConfirmDialog>
    </section>
  )
}

function AddChannel() {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const add = useAddImportChannel()
  const [channel, setChannel] = useState('')
  const [label, setLabel] = useState('')
  // The API takes 2–300 characters for the channel; it says what is wrong with one.
  const ready = channel.trim().length >= 2 && label.trim() !== '' && !add.isPending

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!ready) return
    add.mutate(
      { channel: channel.trim(), label: label.trim() },
      {
        onSuccess: (added) => {
          toast.success(t('admin.importChannels.addDone', { label: added.label }))
          setChannel('')
          setLabel('')
        },
      },
    )
  }

  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <h2 id={id} className="text-lg font-semibold">
        {t('admin.importChannels.add')}
      </h2>
      <Card>
        <form onSubmit={onSubmit} className="flex flex-col gap-4 p-5">
          <Field
            label={t('admin.importChannels.channel')}
            hint={t('admin.importChannels.channelHint')}
            htmlFor={`${id}-channel`}
            className="sm:max-w-md"
          >
            <TextInput
              id={`${id}-channel`}
              value={channel}
              maxLength={300}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              onChange={(e) => {
                setChannel(e.target.value)
                add.reset()
              }}
            />
          </Field>
          <Field
            label={t('admin.importChannels.label')}
            hint={t('admin.importChannels.labelHint')}
            htmlFor={`${id}-label`}
            className="sm:max-w-md"
          >
            <TextInput
              id={`${id}-label`}
              value={label}
              maxLength={200}
              autoComplete="off"
              onChange={(e) => {
                setLabel(e.target.value)
                add.reset()
              }}
            />
          </Field>
          {add.isError && (
            <p role="alert" className="text-sm text-danger">
              {t('admin.importChannels.addFailed', {
                detail: add.error instanceof ApiError ? add.error.detail : t('common.error'),
              })}
            </p>
          )}
          <Button type="submit" variant="primary" className="self-start" disabled={!ready}>
            {add.isPending ? t('admin.importChannels.adding') : t('admin.importChannels.addAction')}
          </Button>
        </form>
      </Card>
    </section>
  )
}
