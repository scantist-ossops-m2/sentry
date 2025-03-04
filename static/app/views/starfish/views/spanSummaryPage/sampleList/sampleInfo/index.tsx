import type {CSSProperties} from 'react';
import styled from '@emotion/styled';

import {RateUnit} from 'sentry/utils/discover/fields';
import {usePageAlert} from 'sentry/utils/performance/contexts/pageAlert';
import {MutableSearch} from 'sentry/utils/tokenizeSearch';
import {CountCell} from 'sentry/views/starfish/components/tableCells/countCell';
import {DurationCell} from 'sentry/views/starfish/components/tableCells/durationCell';
import {ThroughputCell} from 'sentry/views/starfish/components/tableCells/throughputCell';
import {TimeSpentCell} from 'sentry/views/starfish/components/tableCells/timeSpentCell';
import {useSpanMetrics} from 'sentry/views/starfish/queries/useSpanMetrics';
import {SpanMetricsField} from 'sentry/views/starfish/types';
import {DataTitles, getThroughputTitle} from 'sentry/views/starfish/views/spans/types';
import {Block, BlockContainer} from 'sentry/views/starfish/views/spanSummaryPage/block';

const {SPAN_SELF_TIME, SPAN_OP} = SpanMetricsField;

const DEFAULT_DISPLAYED_METRICS = [
  'spm()',
  `avg(${SPAN_SELF_TIME})`,
  'time_spent_percentage()',
];

type Props = {
  groupId: string;
  transactionName: string;
  displayedMetrics?: string[];
  transactionMethod?: string;
};

function SampleInfo(props: Props) {
  const {groupId, transactionName, transactionMethod} = props;
  const {setPageError} = usePageAlert();

  const displayedMetrics = props.displayedMetrics ?? DEFAULT_DISPLAYED_METRICS;

  const filters = {
    'span.group': groupId,
    transaction: transactionName,
  };

  if (transactionMethod) {
    filters['transaction.method'] = transactionMethod;
  }

  const {data, error} = useSpanMetrics({
    search: MutableSearch.fromQueryObject(filters),
    fields: [
      SPAN_OP,
      'spm()',
      `sum(${SPAN_SELF_TIME})`,
      `avg(${SPAN_SELF_TIME})`,
      'time_spent_percentage()',
      'count()',
    ],
    enabled: Object.values(filters).every(value => Boolean(value)),
    referrer: 'api.starfish.span-summary-panel-metrics',
  });

  const spanMetrics = data[0] ?? {};

  const style: CSSProperties = {
    textAlign: 'left',
  };

  if (error) {
    setPageError(error.message);
  }

  function getDisplayBlock(metric: string) {
    switch (metric) {
      case `avg(${SPAN_SELF_TIME})`:
        return (
          <Block key={metric} title={DataTitles.avg} alignment="left">
            <DurationCell
              containerProps={{style}}
              milliseconds={spanMetrics?.[`avg(${SPAN_SELF_TIME})`]}
            />
          </Block>
        );
      case 'count()':
        return (
          <Block key={metric} title={DataTitles.count} alignment="left">
            <CountCell containerProps={{style}} count={spanMetrics?.['count()']} />
          </Block>
        );
      case 'time_spent_percentage()':
        return (
          <Block key={metric} title={DataTitles.timeSpent} alignment="left">
            <TimeSpentCell
              containerProps={{style}}
              percentage={spanMetrics?.[`time_spent_percentage()`]}
              total={spanMetrics?.[`sum(${SPAN_SELF_TIME})`]}
              op={spanMetrics?.['span.op']}
            />
          </Block>
        );
      case 'spm()':
        return (
          <Block
            key={metric}
            title={getThroughputTitle(spanMetrics?.[SPAN_OP])}
            alignment="left"
          >
            <ThroughputCell
              containerProps={{style}}
              rate={spanMetrics?.['spm()']}
              unit={RateUnit.PER_MINUTE}
            />
          </Block>
        );
      default:
        return null;
    }
  }

  return (
    <SampleInfoContainer>
      <BlockContainer>
        {displayedMetrics.map(metric => getDisplayBlock(metric))}
      </BlockContainer>
    </SampleInfoContainer>
  );
}

const SampleInfoContainer = styled('div')`
  display: flex;
`;

export default SampleInfo;
