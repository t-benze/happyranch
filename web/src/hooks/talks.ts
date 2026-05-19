/** Provider-aware talks hooks. Mirrors `hooks/tasks.ts`. */
import { useData } from '@/design-system/providers/DataContext';

export const useTalksRoutes = () => useData().useTalksRoutes();

export const useTalksList: ReturnType<typeof useData>['talks']['useTalksList'] = (
  params,
) => useData().talks.useTalksList(params);

export const useTalk: ReturnType<typeof useData>['talks']['useTalk'] = (talkId) =>
  useData().talks.useTalk(talkId);

export const useStartTalk: ReturnType<typeof useData>['talks']['useStartTalk'] = () =>
  useData().talks.useStartTalk();

export const useAbandonTalk: ReturnType<typeof useData>['talks']['useAbandonTalk'] = (
  talkId,
) => useData().talks.useAbandonTalk(talkId);

export const useEndTalk: ReturnType<typeof useData>['talks']['useEndTalk'] = (
  talkId,
) => useData().talks.useEndTalk(talkId);

export const useDispatchFromTalk: ReturnType<typeof useData>['talks']['useDispatchFromTalk'] = (
  talkId,
) => useData().talks.useDispatchFromTalk(talkId);
