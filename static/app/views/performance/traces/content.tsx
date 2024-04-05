import {Fragment, useCallback, useMemo, useState} from 'react';
import {browserHistory} from 'react-router';
import styled from '@emotion/styled';

import type {GridColumnOrder} from 'sentry/components/gridEditable';
import GridEditable, {COL_WIDTH_UNDEFINED} from 'sentry/components/gridEditable';
import SortLink from 'sentry/components/gridEditable/sortLink';
import * as Layout from 'sentry/components/layouts/thirds';
import {DatePageFilter} from 'sentry/components/organizations/datePageFilter';
import {EnvironmentPageFilter} from 'sentry/components/organizations/environmentPageFilter';
import PageFilterBar from 'sentry/components/organizations/pageFilterBar';
import {ProjectPageFilter} from 'sentry/components/organizations/projectPageFilter';
import type {CursorHandler} from 'sentry/components/pagination';
import Pagination from 'sentry/components/pagination';
import QuestionTooltip from 'sentry/components/questionTooltip';
import {SegmentedControl} from 'sentry/components/segmentedControl';
import type {SmartSearchBarProps} from 'sentry/components/smartSearchBar';
import {t} from 'sentry/locale';
import {space} from 'sentry/styles/space';
import EventView from 'sentry/utils/discover/eventView';
import type {Sort} from 'sentry/utils/discover/fields';
import {DiscoverDatasets} from 'sentry/utils/discover/types';
import {decodeInteger, decodeScalar} from 'sentry/utils/queryString';
import {MutableSearch} from 'sentry/utils/tokenizeSearch';
import {useLocation} from 'sentry/utils/useLocation';
import {useIndexedSpans} from 'sentry/views/starfish/queries/useIndexedSpans';
import type {SpanIndexedFieldTypes} from 'sentry/views/starfish/types';
import {useSpansQuery} from 'sentry/views/starfish/utils/useSpansQuery';

import {getFieldRenderer} from './table/fieldRenderers';
import {fields} from './data';
import {TracesSearchBar} from './tracesSearchBar';

const DEFAULT_PER_PAGE = 20;

type Version = 'v1' | 'v2';

export function Content() {
  const [version, setVersion] = useState<Version>('v1');

  return (
    <LayoutMain fullWidth>
      <PageFilterBar condensed>
        <ProjectPageFilter />
        <EnvironmentPageFilter />
        <DatePageFilter />
      </PageFilterBar>
      <SegmentedControl
        aria-label={t('Trace Explorer Options')}
        value={version}
        onChange={v => setVersion(v as Version)}
      >
        <SegmentedControl.Item key="v1">
          {t('Show Individual Spans (Option 1)')}
        </SegmentedControl.Item>
        <SegmentedControl.Item key="v2">
          {t('Show Rollups to Trace/Transaction Root (Option 2)')}
        </SegmentedControl.Item>
      </SegmentedControl>
      {version === 'v1' && <Option1 />}
      {version === 'v2' && <Option2 />}
    </LayoutMain>
  );
}

type SpanType = 'trace root' | 'transaction root' | 'all';

function Option1() {
  const [spanType, setSpanType] = useState<SpanType>('trace root');

  const location = useLocation();

  const query = useMemo(() => {
    return decodeScalar(location.query.query, '');
  }, [location.query.query]);

  const handleSearch: SmartSearchBarProps['onSearch'] = useCallback(
    (searchQuery: string) => {
      browserHistory.push({
        ...location,
        query: {
          ...location.query,
          cursor: undefined,
          query: searchQuery || undefined,
        },
      });
    },
    [location]
  );

  const handleCursor: CursorHandler = useCallback((newCursor, pathname, newQuery) => {
    browserHistory.push({
      pathname,
      query: {...newQuery, cursor: newCursor},
    });
  }, []);

  const filters = useMemo(() => {
    const search = new MutableSearch(query ?? '');
    if (spanType === 'trace root') {
      search.addFilterValue('parent_span', '00');
    } else if (spanType === 'transaction root') {
      search.addFilterValue('is_segment', '1'); // TODO: this doesnt work yet
    }
    return search.filters;
  }, [spanType, query]);

  const currentSort: Sort = useMemo(() => {
    const value = decodeScalar(location.query.sort, '');
    if (!value) {
      return {field: 'timestamp', kind: 'desc'};
    }
    const kind: 'asc' | 'desc' = value[0] === '-' ? 'desc' : 'asc';
    const field = kind === 'asc' ? value : value.substring(1);

    return {field, kind};
  }, [location.query]);

  const generateSortLink = useCallback(
    (field: string) => () => {
      let sort = `-${field}`;
      if (currentSort.field === field) {
        if (currentSort.kind === 'desc') {
          sort = field;
        }
      }
      return {
        ...location,
        query: {
          ...location.query,
          sort,
        },
      };
    },
    [currentSort, location]
  );

  const limit = useMemo(() => {
    return decodeInteger(location.query.perPage, DEFAULT_PER_PAGE);
  }, [location.query.perPage]);

  const spansQuery = useIndexedSpans({
    fields,
    filters,
    limit,
    sorts: [currentSort],
    referrer: 'api.trace-explorer.option-1.table',
  });

  const spans = useMemo(() => {
    return spansQuery.data ?? [];
  }, [spansQuery]);

  const columnOrder: GridColumnOrder<any>[] = useMemo(() => {
    return fields.map(field => {
      return {
        key: field,
        width: COL_WIDTH_UNDEFINED,
        name: field, // TODO: add more user friendly names for them
      };
    });
  }, []);

  return (
    <Fragment>
      <SegmentedControl
        aria-label={t('Search Only Span Type')}
        value={spanType}
        onChange={st => setSpanType(st as SpanType)}
      >
        <SegmentedControl.Item key="trace root">
          {t('Search Only Trace Roots')}
          <QuestionTooltip
            size="xs"
            position="top"
            title={t(
              'This will only search for spans that have no parent spans. There should be exactly 1 of these per trace.'
            )}
          />
        </SegmentedControl.Item>
        <SegmentedControl.Item key="transaction root">
          {t('Search Only Transaction Roots (Includes Trace Roots)')}
          <QuestionTooltip
            size="xs"
            position="top"
            title={t(
              'This will only search for spans that are entry points into services. There should be exactly 1 of these per application boundary and the trace root is included.'
            )}
          />
        </SegmentedControl.Item>
        <SegmentedControl.Item key="all">
          {t('Show all spans')}
          <QuestionTooltip
            size="xs"
            position="top"
            title={t('This will search every single span that was sent.')}
          />
        </SegmentedControl.Item>
      </SegmentedControl>
      <TracesSearchBar query={query} handleSearch={handleSearch} />
      <GridEditable
        isLoading={spansQuery.isFetching}
        columnOrder={columnOrder}
        columnSortBy={[]}
        data={spans}
        grid={{
          renderHeadCell: renderHeadCell(generateSortLink, currentSort),
          renderBodyCell: renderBodyCell(),
        }}
        location={location}
      />
      <StyledPagination pageLinks={spansQuery.pageLinks} onCursor={handleCursor} />
    </Fragment>
  );
}

function renderHeadCell(generateSortLink, currentSort) {
  return function (col: GridColumnOrder<any>) {
    return (
      <SortLink
        align="left"
        canSort
        direction={col.key === currentSort.field ? currentSort.kind : undefined}
        generateSortLink={generateSortLink(col.key)}
        title={col.name}
      />
    );
  };
}

function renderBodyCell() {
  return function (column: GridColumnOrder<any>, row: any) {
    const Renderer = getFieldRenderer(column.key);
    return <Renderer column={column} row={row} />;
  };
}

function Option2() {
  const [spanType, setSpanType] = useState<SpanType>('trace root');

  const location = useLocation();

  const query = useMemo(() => {
    return decodeScalar(location.query.query, '');
  }, [location.query.query]);

  const handleSearch: SmartSearchBarProps['onSearch'] = useCallback(
    (searchQuery: string) => {
      browserHistory.push({
        ...location,
        query: {
          ...location.query,
          cursor: undefined,
          query: searchQuery || undefined,
        },
      });
    },
    [location]
  );

  const handleCursor: CursorHandler = useCallback((newCursor, pathname, newQuery) => {
    browserHistory.push({
      pathname,
      query: {...newQuery, cursor: newCursor},
    });
  }, []);

  const filters = useMemo(() => {
    const search = new MutableSearch(query ?? '');
    return search.filters;
  }, [query]);

  const limit = useMemo(() => {
    return decodeInteger(location.query.perPage, DEFAULT_PER_PAGE);
  }, [location.query.perPage]);

  const fields2 = useMemo(() => {
    if (spanType === 'trace root') {
      return ['trace', 'count()'];
    }
    return ['project', 'trace', 'transaction.id', 'count()'];
  }, [spanType]);

  const primaryQuery = useIndexedSpans({
    fields: fields2 as any, // whatever
    filters,
    limit,
    sorts: [],
    referrer: 'api.trace-explorer.option-2.primary',
  });

  const eventView = useMemo(() => {
    const field = spanType === 'trace root' ? 'trace' : 'transaction.id';
    const values = [] as string[];
    for (const row of primaryQuery.data ?? []) {
      values.push(row[field]);
    }
    return EventView.fromNewQueryWithLocation(
      {
        name: '',
        query: `${field}:[${values.join(', ')}]`,
        fields: fields2,
        dataset: DiscoverDatasets.SPANS_INDEXED,
        version: 2,
      },
      location
    );
  }, [fields2, location, primaryQuery.data, spanType]);

  const secondaryQuery = useSpansQuery<SpanIndexedFieldTypes[]>({
    eventView,
    cursor: '0:0:0', // hardcoded the cursor here cuz it gets loaded from the url somehow
    limit,
    referrer: 'api.trace-explorer.option-2.secondary',
    enabled: (primaryQuery.data?.length ?? 0) > 0,
  });

  const results = useMemo(() => {
    const ret = {};
    const key = spanType === 'trace root' ? 'trace' : 'transaction.id';
    for (const row of primaryQuery.data ?? []) {
      const group = {
        project: row.project,
        trace: row.trace,
        'transaction.id': row['transaction.id'],
        'matching spans': row['count()'],
      };
      ret[row[key]] = group;
    }
    for (const row of secondaryQuery.data ?? []) {
      const group = ret[row[key]];
      group['all spans'] = row['count()'];
    }
    return Object.values(ret);
  }, [primaryQuery, secondaryQuery, spanType]);

  const columnOrder: any[] = useMemo(
    () =>
      [
        spanType === 'trace root'
          ? {
              key: 'trace',
              width: COL_WIDTH_UNDEFINED,
              name: 'trace',
            }
          : undefined,
        spanType === 'transaction root'
          ? {
              key: 'project',
              width: COL_WIDTH_UNDEFINED,
              name: 'project',
            }
          : undefined,
        spanType === 'transaction root'
          ? {
              key: 'transaction.id',
              width: COL_WIDTH_UNDEFINED,
              name: 'transaction.id',
            }
          : undefined,
        {
          key: 'matching spans',
          width: COL_WIDTH_UNDEFINED,
          name: 'matching spans',
        },
        {
          key: 'all spans',
          width: COL_WIDTH_UNDEFINED,
          name: 'all spans',
        },
      ].filter(Boolean),
    [spanType]
  );

  return (
    <Fragment>
      <SegmentedControl
        aria-label={t('Rollup Span Type')}
        value={spanType}
        onChange={st => setSpanType(st as SpanType)}
      >
        <SegmentedControl.Item key="trace root">
          {t('Roll Up By Trace Roots')}
          <QuestionTooltip
            size="xs"
            position="top"
            title={t(
              'Searches for trace roots where at least 1 span matches the search ALL criterias.'
            )}
          />
        </SegmentedControl.Item>
        <SegmentedControl.Item key="transaction root">
          {t('Roll Up By Transaction Root')}
          <QuestionTooltip
            size="xs"
            position="top"
            title={t(
              'Searches for transaction roots where at least 1 span matches the search ALL criterias.'
            )}
          />
        </SegmentedControl.Item>
      </SegmentedControl>
      <TracesSearchBar query={query} handleSearch={handleSearch} />
      <GridEditable
        isLoading={primaryQuery.isFetching || secondaryQuery.isFetching}
        columnOrder={columnOrder}
        columnSortBy={[]}
        data={results}
        grid={{
          renderBodyCell: renderBodyCell(),
        }}
        location={location}
      />
      <StyledPagination pageLinks={primaryQuery.pageLinks} onCursor={handleCursor} />
    </Fragment>
  );
}

const LayoutMain = styled(Layout.Main)`
  display: flex;
  flex-direction: column;
  gap: ${space(2)};
`;

const StyledPagination = styled(Pagination)`
  margin: 0px;
`;
