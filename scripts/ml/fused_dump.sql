-- What scripts/ml/fused_eval.py reads about one video, as one JSON object (read-only; PLAN §10.3).
--
--     ssh <db host> "docker exec -i <postgres> psql -U <user> -d wieplay -At -v vid=<video id>" \
--         < scripts/ml/fused_dump.sql > DUMP.json
--
-- Rallies, sets, the coach's serve tags, the newest track of each kind, the game's official
-- sets, the roster's numbers and the typed scoresheet. Club members' data: keep the output
-- outside git and delete it after the measurement (PLAN §5.5).
select json_build_object(
  'video', (select json_build_object('id', v.id, 'title', v.title, 'game_id', v.game_id)
            from video v where v.id = :'vid'),
  'game', (select json_build_object('id', g.id, 'our_side', g.our_side, 'sets', g.sets)
           from wd_game g join video v on v.game_id = g.id where v.id = :'vid'),
  'rallies', (select coalesce(json_agg(json_build_object(
                'id', s.id, 'kind', s.kind, 'start_ms', s.start_ms, 'end_ms', s.end_ms,
                'source', s.source, 'review', s.review, 'confidence', s.confidence,
                'won_by', s.won_by, 'serving_manual', s.serving_manual,
                'deleted_at', s.deleted_at) order by s.start_ms, s.id), '[]')
              from segment s where s.video_id = :'vid' and s.kind = 'rally'),
  'periods', (select coalesce(json_agg(json_build_object(
                'id', p.id, 'no', p.no, 'start_ms', p.start_ms, 'end_ms', p.end_ms,
                'start_us', p.start_us, 'start_them', p.start_them,
                'first_serve', p.first_serve, 'our_court_side_at_start', p.our_court_side_at_start,
                'side_switch_at_score', p.side_switch_at_score, 'source', p.source,
                'review', p.review) order by p.start_ms), '[]')
              from period p where p.video_id = :'vid' and p.deleted_at is null),
  'serves', (select coalesce(json_agg(json_build_object('t_ms', e.t_ms, 'side', e.side)
                                      order by e.t_ms), '[]')
             from event e where e.video_id = :'vid' and e.code = 'S' and e.deleted_at is null
               and (e.source <> 'model' or e.review in ('confirmed', 'corrected'))),
  'tracks', (select coalesce(json_object_agg(t.kind, json_build_object(
                'step_ms', t.step_ms, 'channels', t.channels)), '{}')
             from (select distinct on (kind) kind, step_ms, channels from analysis_track
                   where video_id = :'vid' order by kind, created_at desc) t),
  'roster', (select coalesce(json_agg(json_build_object('side', r.side, 'number', r.number)), '[]')
             from video_roster r where r.video_id = :'vid'),
  'scoresheet', (select json_build_object('version', x.version, 'sets', x.sets)
                 from scoresheet x where x.video_id = :'vid' and x.deleted_at is null)
);
