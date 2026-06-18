import type { WorkHoursApi, QueryLike } from './DataContext';
import type { WorkHourRecord } from '@/lib/api/types';

const empty: QueryLike<{ work_hours: WorkHourRecord[] }> = {
  data: { work_hours: [] },
  isLoading: false,
  isError: false,
  error: null,
};

export const mockWorkHoursApi: WorkHoursApi = {
  useWorkHoursList: () => empty,
};
