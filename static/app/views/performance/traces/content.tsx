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
import {defined} from 'sentry/utils';
import type {Sort} from 'sentry/utils/discover/fields';
import {decodeInteger, decodeScalar} from 'sentry/utils/queryString';
import {MutableSearch} from 'sentry/utils/tokenizeSearch';
import {useLocation} from 'sentry/utils/useLocation';
import {useIndexedSpans} from 'sentry/views/starfish/queries/useIndexedSpans';

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
    referrer: 'api.trace-explorer.table',
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
    return ['trace', 'transaction.id', 'count()'];
  }, [spanType]);

  const primaryQuery = useIndexedSpans({
    fields: fields2 as any, // whatever
    filters,
    limit,
    sorts: [],
    referrer: 'api.trace-explorer.table',
  });

  const groupFilters = useMemo(() => {
    const filter = {
      trace: [] as string[],
      'transaction.id': [] as string[],
    };
    for (const row of primaryQuery.data ?? []) {
      filter.trace.push(row.trace);
      if (spanType === 'transaction root') {
        filter['transaction.id'].push(row['transaction.id']);
      }
    }
    return filter;
  }, [primaryQuery.data, spanType]);

  useIndexedSpans({
    fields: [...fields2, 'max(timestamp)', 'min(timestamp)'] as any[],
    filters: groupFilters,
    limit,
    sorts: [],
    referrer: 'api.trace-explorer.table',
    enabled: defined(primaryQuery.data),
  });

  // const rollupFilter = useMemo(() => {
  //   console.log(spansQuery.data ?? []);

  //   const seen = new Set();
  //   for (const span of spansQuery.data ?? []) {
  //     if (spanType === 'trace root') {
  //       seen.add(span.trace);
  //     } else {
  //       seen.add(span['transaction.id']);
  //     }
  //   }
  //   console.log(seen);
  // }, [spansQuery.data, spanType]);

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
