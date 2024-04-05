import type {FC} from 'react';
import {Fragment} from 'react';

import type {GridColumnOrder} from 'sentry/components/gridEditable';
import ProjectBadge from 'sentry/components/idBadge/projectBadge';
import Link from 'sentry/components/links/link';
import PerformanceDuration from 'sentry/components/performanceDuration';
import type {DateString} from 'sentry/types';
import {defined} from 'sentry/utils';
import {Container, FieldDateTime} from 'sentry/utils/discover/styles';
import {getShortEventId} from 'sentry/utils/events';
import {getTransactionDetailsUrl} from 'sentry/utils/performance/urls';
import Projects from 'sentry/utils/projects';
import {decodeScalar} from 'sentry/utils/queryString';
import {useLocation} from 'sentry/utils/useLocation';
import useOrganization from 'sentry/utils/useOrganization';
import usePageFilters from 'sentry/utils/usePageFilters';
import useProjects from 'sentry/utils/useProjects';
import {getTraceDetailsUrl} from 'sentry/views/performance/traceDetails/utils';
import {transactionSummaryRouteWithQuery} from 'sentry/views/performance/transactionSummary/utils';

export function getFieldRenderer(field: string): FC<FieldRendererProps> {
  return fieldRenderers[field] ?? DefaultRenderer;
}

const fieldRenderers = {
  project: ProjectFieldRenderer,
  span_id: SpanIdFieldRenderer,
  trace: TraceIdFieldRenderer,
  'transaction.id': TransactionIdFieldRenderer,
  transaction: TransactionFieldRenderer,
  timestamp: TimestampFieldRenderer,
  'span.duration': SpanDurationFieldRenderer,
  'span.self_time': SpanSelfTimeFieldRenderer,
};

interface FieldRendererProps {
  column: GridColumnOrder<any>;
  row: any;
}

function DefaultRenderer({row, column}: FieldRendererProps) {
  // TODO: this can be smarter based on the type of the value
  return <Container>{row[column.key]}</Container>;
}

function ProjectFieldRenderer(props: FieldRendererProps) {
  const projectSlug = props.row.project;

  if (!defined(projectSlug)) {
    return <DefaultRenderer {...props} />;
  }

  return <ProjectRenderer {...props} projectSlug={projectSlug} />;
}

function SpanIdFieldRenderer(props: FieldRendererProps) {
  const projectSlug = props.row.project;
  const spanId = props.row.span_id;
  const transactionId = props.row['transaction.id'];

  if (!defined(projectSlug) || !defined(spanId)) {
    return <DefaultRenderer {...props} />;
  }

  return (
    <SpanIdRenderer
      {...props}
      projectSlug={projectSlug}
      spanId={spanId}
      transactionId={transactionId}
    />
  );
}

function TraceIdFieldRenderer(props: FieldRendererProps) {
  const traceId = props.row.trace;

  if (!defined(traceId)) {
    return <DefaultRenderer {...props} />;
  }

  return (
    <TraceIdRenderer
      {...props}
      traceId={traceId}
      transactionId={props.row['transaction.id'] ?? undefined}
      timestamp={props.row.timestamp}
    />
  );
}

function TransactionIdFieldRenderer(props: FieldRendererProps) {
  const projectSlug = props.row.project;
  const transactionId = props.row['transaction.id'];

  if (!defined(projectSlug) || !defined(transactionId)) {
    return <DefaultRenderer {...props} />;
  }

  return (
    <TransactionIdRenderer
      {...props}
      projectSlug={projectSlug}
      transactionId={transactionId}
    />
  );
}

function TransactionFieldRenderer(props: FieldRendererProps) {
  const projectSlug = props.row.project;
  const transaction = props.row.transaction;

  if (!defined(projectSlug) || !defined(transaction)) {
    return <DefaultRenderer {...props} />;
  }

  return (
    <TransactionRenderer {...props} projectSlug={projectSlug} transaction={transaction} />
  );
}

function TimestampFieldRenderer(props: FieldRendererProps) {
  const location = useLocation();
  const timestamp = props.row.timestamp;

  if (!defined(timestamp)) {
    return <DefaultRenderer {...props} />;
  }

  const utc = decodeScalar(location?.query?.utc) === 'true';

  return <FieldDateTime date={timestamp} year seconds timeZone utc={utc} />;
}

function SpanDurationFieldRenderer(props: FieldRendererProps) {
  const duration = props.row['span.duration'];

  if (!defined(duration)) {
    return <DefaultRenderer {...props} />;
  }

  return <PerformanceDuration milliseconds={duration} abbreviation />;
}

function SpanSelfTimeFieldRenderer(props: FieldRendererProps) {
  const duration = props.row['span.self_time'];

  if (!defined(duration)) {
    return <DefaultRenderer {...props} />;
  }

  return <PerformanceDuration milliseconds={duration} abbreviation />;
}

interface ProjectRendererProps {
  projectSlug: string;
}

export function ProjectRenderer({projectSlug}: ProjectRendererProps) {
  const organization = useOrganization();

  return (
    <Container>
      <Projects orgId={organization.slug} slugs={[projectSlug]}>
        {({projects}) => {
          const project = projects.find(p => p.slug === projectSlug);
          return (
            <ProjectBadge
              project={project ? project : {slug: projectSlug}}
              avatarSize={16}
            />
          );
        }}
      </Projects>
    </Container>
  );
}

interface SpanIdRendererProps {
  projectSlug: string;
  spanId: string;
  transactionId?: string;
}

export function SpanIdRenderer({
  projectSlug,
  spanId,
  transactionId,
}: SpanIdRendererProps) {
  const organization = useOrganization();

  if (!defined(transactionId)) {
    return <Fragment>{getShortEventId(spanId)}</Fragment>;
  }

  const target = getTransactionDetailsUrl(
    organization.slug,
    `${projectSlug}:${transactionId}`,
    undefined,
    undefined,
    spanId
  );

  return <Link to={target}>{getShortEventId(spanId)}</Link>;
}

interface TraceIdRendererProps {
  traceId: string;
  timestamp?: DateString;
  transactionId?: string;
}

export function TraceIdRenderer({
  traceId,
  timestamp,
  transactionId,
}: TraceIdRendererProps) {
  const organization = useOrganization();
  const {selection} = usePageFilters();
  const stringOrNumberTimestamp =
    timestamp instanceof Date ? timestamp.toISOString() : timestamp ?? '';

  const target = getTraceDetailsUrl(
    organization,
    traceId,
    {
      start: selection.datetime.start,
      end: selection.datetime.end,
      statsPeriod: selection.datetime.period,
    },
    {},
    stringOrNumberTimestamp,
    transactionId
  );

  return (
    <Container>
      <Link to={target}>{getShortEventId(traceId)}</Link>
    </Container>
  );
}

interface TransactionIdRendererProps {
  projectSlug: string;
  transactionId: string;
}

export function TransactionIdRenderer({
  projectSlug,
  transactionId,
}: TransactionIdRendererProps) {
  const organization = useOrganization();

  const target = getTransactionDetailsUrl(
    organization.slug,
    `${projectSlug}:${transactionId}`,
    undefined,
    undefined
  );

  return <Link to={target}>{getShortEventId(transactionId)}</Link>;
}

interface TransactionRendererProps {
  projectSlug: string;
  transaction: string;
}

export function TransactionRenderer({
  projectSlug,
  transaction,
}: TransactionRendererProps) {
  const location = useLocation();
  const organization = useOrganization();
  const {projects} = useProjects({slugs: [projectSlug]});

  const target = transactionSummaryRouteWithQuery({
    orgSlug: organization.slug,
    transaction,
    query: {
      ...location.query,
      query: undefined,
    },
    projectID: String(projects[0]?.id ?? ''),
  });

  return (
    <Container>
      <Link to={target}>{transaction}</Link>
    </Container>
  );
}
