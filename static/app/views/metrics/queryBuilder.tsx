import {Fragment, memo, useCallback, useEffect, useMemo, useState} from 'react';
import styled from '@emotion/styled';
import uniqBy from 'lodash/uniqBy';

import {ComboBox} from 'sentry/components/comboBox';
import type {ComboBoxOption} from 'sentry/components/comboBox/types';
import type {SelectOption} from 'sentry/components/compactSelect';
import {CompactSelect} from 'sentry/components/compactSelect';
import {Tag} from 'sentry/components/tag';
import {IconLightning, IconReleases} from 'sentry/icons';
import {t} from 'sentry/locale';
import {space} from 'sentry/styles/space';
import type {MetricMeta, MetricsOperation, MRI} from 'sentry/types';
import {trackAnalytics} from 'sentry/utils/analytics';
import {
  isAllowedOp,
  isCustomMetric,
  isSpanMeasurement,
  isSpanSelfTime,
  isTransactionDuration,
  isTransactionMeasurement,
} from 'sentry/utils/metrics';
import {getReadableMetricType} from 'sentry/utils/metrics/formatters';
import {formatMRI, parseMRI} from 'sentry/utils/metrics/mri';
import type {MetricsQuery} from 'sentry/utils/metrics/types';
import {useIncrementQueryMetric} from 'sentry/utils/metrics/useIncrementQueryMetric';
import {useMetricsMeta} from 'sentry/utils/metrics/useMetricsMeta';
import {useMetricsTags} from 'sentry/utils/metrics/useMetricsTags';
import useKeyPress from 'sentry/utils/useKeyPress';
import useOrganization from 'sentry/utils/useOrganization';
import usePageFilters from 'sentry/utils/usePageFilters';
import {MetricSearchBar} from 'sentry/views/metrics/metricSearchBar';

type QueryBuilderProps = {
  metricsQuery: MetricsQuery;
  onChange: (data: Partial<MetricsQuery>) => void;
  projects: number[];
};

const isVisibleTransactionMetric = (metric: MetricMeta) =>
  isTransactionDuration(metric) || isTransactionMeasurement(metric);

const isVisibleSpanMetric = (metric: MetricMeta) =>
  isSpanSelfTime(metric) || isSpanMeasurement(metric);

const isShownByDefault = (metric: MetricMeta) =>
  isCustomMetric(metric) ||
  isVisibleTransactionMetric(metric) ||
  isVisibleSpanMetric(metric);

function getOpsForMRI(mri: MRI, meta: MetricMeta[]) {
  return meta.find(metric => metric.mri === mri)?.operations.filter(isAllowedOp) ?? [];
}

function useMriMode() {
  const [mriMode, setMriMode] = useState(false);
  const mriModeKeyPressed = useKeyPress('`', undefined, true);

  useEffect(() => {
    if (mriModeKeyPressed) {
      setMriMode(value => !value);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mriModeKeyPressed]);

  return mriMode;
}

export const QueryBuilder = memo(function QueryBuilder({
  metricsQuery,
  projects,
  onChange,
}: QueryBuilderProps) {
  const organization = useOrganization();
  const pageFilters = usePageFilters();
  const {data: meta, isLoading: isMetaLoading} = useMetricsMeta(pageFilters.selection);
  const mriMode = useMriMode();

  const {data: tagsData = [], isLoading: tagsIsLoading} = useMetricsTags(
    metricsQuery.mri,
    {
      projects,
    }
  );

  const tags = useMemo(() => {
    return uniqBy(tagsData, 'key');
  }, [tagsData]);

  const displayedMetrics = useMemo(() => {
    const isSelected = (metric: MetricMeta) => metric.mri === metricsQuery.mri;
    const result = meta
      .filter(metric => isShownByDefault(metric) || isSelected(metric))
      .sort(metric => (isSelected(metric) ? -1 : 1));

    // Add the selected metric to the top of the list if it's not already there
    if (result[0]?.mri !== metricsQuery.mri) {
      const parsedMri = parseMRI(metricsQuery.mri)!;
      return [
        {
          mri: metricsQuery.mri,
          type: parsedMri.type,
          unit: parsedMri.unit,
          operations: getOpsForMRI(metricsQuery.mri, meta),
        },
        ...result,
      ];
    }

    return result;
  }, [meta, metricsQuery.mri]);

  const selectedMeta = useMemo(() => {
    return meta.find(metric => metric.mri === metricsQuery.mri);
  }, [meta, metricsQuery.mri]);

  const incrementQueryMetric = useIncrementQueryMetric({
    ...metricsQuery,
  });

  const handleMRIChange = useCallback(
    ({value}) => {
      const availableOps = getOpsForMRI(value, meta);
      const selectedOp = availableOps.includes(
        (metricsQuery.op ?? '') as MetricsOperation
      )
        ? metricsQuery.op
        : availableOps?.[0];

      const queryChanges = {
        mri: value,
        op: selectedOp,
        groupBy: undefined,
      };

      trackAnalytics('ddm.widget.metric', {organization});
      incrementQueryMetric('ddm.widget.metric', queryChanges);
      onChange(queryChanges);
    },
    [incrementQueryMetric, meta, metricsQuery.op, onChange, organization]
  );

  const handleOpChange = useCallback(
    ({value}) => {
      trackAnalytics('ddm.widget.operation', {organization});
      incrementQueryMetric('ddm.widget.operation', {op: value});
      onChange({
        op: value,
      });
    },
    [incrementQueryMetric, onChange, organization]
  );

  const handleGroupByChange = useCallback(
    (options: SelectOption<string>[]) => {
      trackAnalytics('ddm.widget.group', {organization});
      incrementQueryMetric('ddm.widget.group', {
        groupBy: options.map(o => o.value),
      });
      onChange({
        groupBy: options.map(o => o.value),
      });
    },
    [incrementQueryMetric, onChange, organization]
  );

  const handleQueryChange = useCallback(
    (query: string) => {
      trackAnalytics('ddm.widget.filter', {organization});
      incrementQueryMetric('ddm.widget.filter', {query});
      onChange({query});
    },
    [incrementQueryMetric, onChange, organization]
  );

  const mriOptions = useMemo(
    () =>
      displayedMetrics.map<ComboBoxOption<MRI>>(metric => ({
        label: mriMode ? metric.mri : formatMRI(metric.mri),
        // enable search by mri, name, unit (millisecond), type (c:), and readable type (counter)
        textValue: `${metric.mri}${getReadableMetricType(metric.type)}`,
        value: metric.mri,
        trailingItems: mriMode ? undefined : (
          <Fragment>
            <Tag tooltipText={t('Type')}>{getReadableMetricType(metric.type)}</Tag>
            <Tag tooltipText={t('Unit')}>{metric.unit}</Tag>
          </Fragment>
        ),
      })),
    [displayedMetrics, mriMode]
  );

  const projectIdStrings = useMemo(() => projects.map(String), [projects]);

  return (
    <QueryBuilderWrapper>
      <FlexBlock>
        <MetricSelect
          aria-label={t('Metric')}
          placeholder={t('Select a metric')}
          sizeLimit={100}
          size="md"
          isLoading={isMetaLoading}
          options={mriOptions}
          value={metricsQuery.mri}
          onChange={handleMRIChange}
        />
        <FlexBlock>
          <OpSelect
            size="md"
            triggerProps={{prefix: t('Agg')}}
            options={
              selectedMeta?.operations.filter(isAllowedOp).map(op => ({
                label: op,
                value: op,
              })) ?? []
            }
            triggerLabel={metricsQuery.op}
            disabled={!selectedMeta}
            value={metricsQuery.op}
            onChange={handleOpChange}
          />
          <CompactSelect
            multiple
            size="md"
            triggerProps={{prefix: t('Group by')}}
            options={tags.map(tag => ({
              label: tag.key,
              value: tag.key,
              trailingItems: (
                <Fragment>
                  {tag.key === 'release' && <IconReleases size="xs" />}
                  {tag.key === 'transaction' && <IconLightning size="xs" />}
                </Fragment>
              ),
            }))}
            disabled={!metricsQuery.mri || tagsIsLoading}
            value={metricsQuery.groupBy}
            onChange={handleGroupByChange}
          />
        </FlexBlock>
      </FlexBlock>
      <SearchBarWrapper>
        <MetricSearchBar
          mri={metricsQuery.mri}
          disabled={!metricsQuery.mri}
          onChange={handleQueryChange}
          query={metricsQuery.query}
          projectIds={projectIdStrings}
          blockedTags={selectedMeta?.blockingStatus?.flatMap(s => s.blockedTags) ?? []}
        />
      </SearchBarWrapper>
    </QueryBuilderWrapper>
  );
});

const QueryBuilderWrapper = styled('div')`
  display: flex;
  flex-grow: 1;
  gap: ${space(1)};
  flex-wrap: wrap;
`;

const FlexBlock = styled('div')`
  display: flex;
  gap: ${space(1)};
  flex-wrap: wrap;
`;

const MetricSelect = styled(ComboBox)`
  min-width: 200px;
  & > button {
    width: 100%;
  }
`;

const OpSelect = styled(CompactSelect)`
  width: 128px;
  min-width: min-content;
  & > button {
    width: 100%;
  }
`;

const SearchBarWrapper = styled('div')`
  flex: 1;
  min-width: 200px;
`;
