import {
  feedbackOnboardingPlatforms,
  replayOnboardingPlatforms,
  withoutPerformanceSupport,
} from 'sentry/data/platformCategories';
import {type OnboardingTask, OnboardingTaskKey, type Project} from 'sentry/types';

const replayRelatedTasks = [OnboardingTaskKey.SESSION_REPLAY];
const performanceRelatedTasks = [
  OnboardingTaskKey.FIRST_TRANSACTION,
  OnboardingTaskKey.PERFORMANCE_GUIDE,
  OnboardingTaskKey.METRIC_ALERT,
];
const feedbackRelatedTasks = [OnboardingTaskKey.USER_REPORTS];

export function filterSupportedTasks(
  projects: Project[] | undefined,
  allTasks: OnboardingTask[]
): OnboardingTask[] {
  const shouldShowReplayTasks = projects?.some(
    project => project.platform && replayOnboardingPlatforms.includes(project.platform)
  );
  const shouldShowPerformanceTasks = !projects?.every(
    project => project.platform && withoutPerformanceSupport.has(project.platform)
  );
  const shouldShowFeedbackTasks = projects?.some(
    project => project.platform && feedbackOnboardingPlatforms.includes(project.platform)
  );

  // Remove tasks for features that are not supported
  return allTasks.filter(
    task =>
      (shouldShowReplayTasks || !replayRelatedTasks.includes(task.task)) &&
      (shouldShowPerformanceTasks || !performanceRelatedTasks.includes(task.task)) &&
      (shouldShowFeedbackTasks || !feedbackRelatedTasks.includes(task.task))
  );
}
