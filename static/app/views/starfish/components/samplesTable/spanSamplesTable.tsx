import {Link} from 'react-router';
import styled from '@emotion/styled';

import {LinkButton} from 'sentry/components/button';
import type {GridColumnHeader} from 'sentry/components/gridEditable';
import GridEditable, {COL_WIDTH_UNDEFINED} from 'sentry/components/gridEditable';
import {Tooltip} from 'sentry/components/tooltip';
import {IconProfiling} from 'sentry/icons/iconProfiling';
import {t} from 'sentry/locale';
import EventView from 'sentry/utils/discover/eventView';
import {
  generateEventSlug,
  generateLinkToEventInTraceView,
} from 'sentry/utils/discover/urls';
import {useLocation} from 'sentry/utils/useLocation';
import useOrganization from 'sentry/utils/useOrganization';
import {normalizeUrl} from 'sentry/utils/withDomainRequired';
import {DurationComparisonCell} from 'sentry/views/starfish/components/samplesTable/common';
import {DurationCell} from 'sentry/views/starfish/components/tableCells/durationCell';
import ResourceSizeCell from 'sentry/views/starfish/components/tableCells/resourceSizeCell';
import {
  OverflowEllipsisTextContainer,
  TextAlignRight,
} from 'sentry/views/starfish/components/textAlign';
import type {SpanSample} from 'sentry/views/starfish/queries/useSpanSamples';
import {SpanMetricsField} from 'sentry/views/starfish/types';

const {HTTP_RESPONSE_CONTENT_LENGTH} = SpanMetricsField;

type Keys =
  | 'transaction_id'
  | 'span_id'
  | 'profile_id'
  | 'timestamp'
  | 'duration'
  | 'p95_comparison'
  | 'avg_comparison'
  | 'http.response_content_length';
export type SamplesTableColumnHeader = GridColumnHeader<Keys>;

export const DEFAULT_COLUMN_ORDER: SamplesTableColumnHeader[] = [
  {
    key: 'span_id',
    name: 'Span ID',
    width: COL_WIDTH_UNDEFINED,
  },
  {
    key: 'duration',
    name: 'Span Duration',
    width: COL_WIDTH_UNDEFINED,
  },
  {
    key: 'avg_comparison',
    name: 'Compared to Average',
    width: COL_WIDTH_UNDEFINED,
  },
];

type SpanTableRow = {
  op: string;
  transaction: {
    id: string;
    'project.name': string;
    timestamp: string;
    trace: string;
    'transaction.duration': number;
  };
} & SpanSample;

type Props = {
  avg: number;
  data: SpanTableRow[];
  isLoading: boolean;
  columnOrder?: SamplesTableColumnHeader[];
  highlightedSpanId?: string;
  onMouseLeaveSample?: () => void;
  onMouseOverSample?: (sample: SpanSample) => void;
};

export function SpanSamplesTable({
  isLoading,
  data,
  avg,
  highlightedSpanId,
  onMouseLeaveSample,
  onMouseOverSample,
  columnOrder,
}: Props) {
  const location = useLocation();
  const organization = useOrganization();

  function renderHeadCell(column: GridColumnHeader): React.ReactNode {
    if (
      column.key === 'p95_comparison' ||
      column.key === 'avg_comparison' ||
      column.key === 'duration'
    ) {
      return (
        <TextAlignRight>
          <OverflowEllipsisTextContainer>{column.name}</OverflowEllipsisTextContainer>
        </TextAlignRight>
      );
    }

    return <OverflowEllipsisTextContainer>{column.name}</OverflowEllipsisTextContainer>;
  }

  function renderBodyCell(column: GridColumnHeader, row: SpanTableRow): React.ReactNode {
    if (column.key === 'span_id') {
      return (
        <Link
          to={generateLinkToEventInTraceView({
            eventSlug: generateEventSlug({
              id: row['transaction.id'],
              project: row.project,
            }),
            organization,
            location,
            eventView: EventView.fromLocation(location),
            dataRow: {
              id: row['transaction.id'],
              trace: row.transaction?.trace,
              timestamp: row.timestamp,
            },
            spanId: row.span_id,
          })}
        >
          {row.span_id}
        </Link>
      );
    }

    if (column.key === HTTP_RESPONSE_CONTENT_LENGTH) {
      const size = parseInt(row[HTTP_RESPONSE_CONTENT_LENGTH], 10);
      return <ResourceSizeCell bytes={size} />;
    }

    if (column.key === 'profile_id') {
      return (
        <IconWrapper>
          {row.profile_id ? (
            <Tooltip title={t('View Profile')}>
              <LinkButton
                to={normalizeUrl(
                  `/organizations/${organization.slug}/profiling/profile/${row.project}/${row.profile_id}/flamegraph/?spanId=${row.span_id}`
                )}
                size="xs"
              >
                <IconProfiling size="xs" />
              </LinkButton>
            </Tooltip>
          ) : (
            <div>(no value)</div>
          )}
        </IconWrapper>
      );
    }

    if (column.key === 'duration') {
      return <DurationCell milliseconds={row['span.self_time']} />;
    }

    if (column.key === 'avg_comparison') {
      return (
        <DurationComparisonCell
          duration={row['span.self_time']}
          compareToDuration={avg}
        />
      );
    }

    return <span>{row[column.key]}</span>;
  }

  return (
    <GridEditable
      isLoading={isLoading}
      data={data}
      columnOrder={columnOrder ?? DEFAULT_COLUMN_ORDER}
      columnSortBy={[]}
      onRowMouseOver={onMouseOverSample}
      onRowMouseOut={onMouseLeaveSample}
      highlightedRowKey={data.findIndex(sample => sample.span_id === highlightedSpanId)}
      grid={{
        renderHeadCell,
        renderBodyCell,
      }}
      location={location}
    />
  );
}

const IconWrapper = styled('div')`
  text-align: right;
  width: 100%;
  height: 26px;
`;
