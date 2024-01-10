import {t} from 'sentry/locale';
import {NewQuery} from 'sentry/types';
import EventView, {fromSorts} from 'sentry/utils/discover/eventView';
import {Sort} from 'sentry/utils/discover/fields';
import {DiscoverDatasets} from 'sentry/utils/discover/types';
import {decodeScalar} from 'sentry/utils/queryString';
import {MutableSearch} from 'sentry/utils/tokenizeSearch';
import {useLocation} from 'sentry/utils/useLocation';
import usePageFilters from 'sentry/utils/usePageFilters';
import {formatVersionAndCenterTruncate} from 'sentry/views/starfish/utils/centerTruncate';
import {EventSamplesTable} from 'sentry/views/starfish/views/screens/screenLoadSpans/eventSamplesTable';
import {useTableQuery} from 'sentry/views/starfish/views/screens/screensTable';

const DEFAULT_SORT: Sort = {
  kind: 'desc',
  field: 'span.duration',
};

type Props = {
  cursorName: string;
  release: string;
  sortKey: string;
  transaction: string;
  showDeviceClassSelector?: boolean;
};

export function EventSamples({
  cursorName,
  transaction,
  release,
  sortKey,
  showDeviceClassSelector,
}: Props) {
  const location = useLocation();
  const {selection} = usePageFilters();
  const cursor = decodeScalar(location.query?.[cursorName]);

  const searchQuery = new MutableSearch([
    `transaction:${transaction}`,
    `release:${release}`,
    'span.op:[app.start.cold,app.start.warm]',
    '(',
    'span.description:"Cold Start"',
    'OR',
    'span.description:"Warm Start"',
    ')',
  ]);

  const deviceClass = decodeScalar(location.query['device.class']);

  if (deviceClass) {
    if (deviceClass === 'Unknown') {
      searchQuery.addFilterValue('!has', 'device.class');
    } else {
      searchQuery.addFilterValue('device.class', deviceClass);
    }
  }

  const sort = fromSorts(decodeScalar(location.query[sortKey]))[0] ?? DEFAULT_SORT;

  const columnNameMap = {
    'transaction.id': t('Event ID (%s)', formatVersionAndCenterTruncate(release)),
    profile_id: t('Profile'),
    'span.description': t('Start Type'),
    'span.duration': t('Duration'),
  };

  const newQuery: NewQuery = {
    name: '',
    fields: [
      'transaction.id',
      'project.name',
      'profile_id',
      'span.description',
      'span.duration',
    ],
    query: searchQuery.formatString(),
    dataset: DiscoverDatasets.SPANS_INDEXED,
    version: 2,
    projects: selection.projects,
  };

  const eventView = EventView.fromNewQueryWithLocation(newQuery, location);
  eventView.sorts = [sort];

  const {data, isLoading, pageLinks} = useTableQuery({
    eventView,
    enabled: true,
    limit: 4,
    cursor,
    referrer: 'api.starfish.mobile-startup-event-samples',
  });

  return (
    <EventSamplesTable
      cursorName={cursorName}
      eventIdKey="transaction.id"
      eventView={eventView}
      isLoading={isLoading}
      profileIdKey="profile_id"
      sortKey={sortKey}
      data={data}
      pageLinks={pageLinks}
      showDeviceClassSelector={showDeviceClassSelector}
      columnNameMap={columnNameMap}
      sort={sort}
    />
  );
}