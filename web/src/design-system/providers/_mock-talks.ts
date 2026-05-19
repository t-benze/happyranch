import type {
  MutationLike,
  QueryLike,
  TalksApi,
  TalksRoutes,
} from './DataContext';

const emptyQuery = <T>(data: T): QueryLike<T> => ({
  data,
  isLoading: false,
  isError: false,
  error: null,
});

const noopMutation = <A, R>(): MutationLike<A, R> => ({
  mutateAsync: () =>
    Promise.reject(new Error('talks mutations are disabled in the prototype sandbox')),
  isPending: false,
});

export const mockTalksApi: TalksApi = {
  useTalksList: () => emptyQuery({ talks: [] }),
  useTalk: () => emptyQuery(undefined as never),
  useStartTalk: () => noopMutation(),
  useAbandonTalk: () => noopMutation(),
  useEndTalk: () => noopMutation(),
  useDispatchFromTalk: () => noopMutation(),
};

export function useMockTalksRoutes(): TalksRoutes {
  return {
    inbox: () => '/__prototypes/talks/inbox',
    detail: (talkId: string) => `/__prototypes/talks/${talkId}`,
    inboxForOrg: () => '/__prototypes/talks/inbox',
  };
}
