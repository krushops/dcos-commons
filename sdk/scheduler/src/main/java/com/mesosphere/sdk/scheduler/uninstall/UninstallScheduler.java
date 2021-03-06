package com.mesosphere.sdk.scheduler.uninstall;

import com.google.common.annotations.VisibleForTesting;
import com.mesosphere.sdk.api.PlansResource;
import com.mesosphere.sdk.dcos.SecretsClient;
import com.mesosphere.sdk.offer.*;
import com.mesosphere.sdk.scheduler.*;
import com.mesosphere.sdk.scheduler.plan.*;
import com.mesosphere.sdk.specification.ServiceSpec;
import com.mesosphere.sdk.state.ConfigStore;
import com.mesosphere.sdk.state.StateStore;
import com.mesosphere.sdk.state.StateStoreUtils;

import org.apache.mesos.Protos;
import org.apache.mesos.Scheduler;
import org.apache.mesos.SchedulerDriver;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.*;
import java.util.stream.Collectors;

/**
 * This scheduler uninstalls the framework and releases all of its resources.
 */
public class UninstallScheduler extends AbstractScheduler {

    private static final Logger LOGGER = LoggerFactory.getLogger(UninstallScheduler.class);

    private final UninstallPlanBuilder uninstallPlanBuilder;
    private final PlanManager uninstallPlanManager;
    private final Collection<Object> resources;

    private OfferAccepter offerAccepter;

    /**
     * Creates a new UninstallScheduler based on the provided API port and initialization timeout,
     * and a {@link StateStore}. The UninstallScheduler builds an uninstall {@link Plan} with two {@link Phase}s:
     * a resource phase where all reserved resources get released back to Mesos, and a deregister phase where
     * the framework deregisters itself and cleans up its state in Zookeeper.
     */
    public UninstallScheduler(
            ServiceSpec serviceSpec,
            StateStore stateStore,
            ConfigStore<ServiceSpec> configStore,
            SchedulerFlags schedulerFlags,
            Optional<SecretsClient> secretsClient) {
        super(stateStore, configStore, schedulerFlags);
        uninstallPlanBuilder = new UninstallPlanBuilder(
                serviceSpec, stateStore, configStore, schedulerFlags, secretsClient);
        uninstallPlanManager = new DefaultPlanManager(uninstallPlanBuilder.getPlan());
        resources = Collections.singletonList(new PlansResource()
                .setPlanManagers(Collections.singletonList(uninstallPlanManager)));
    }

    @Override
    public Optional<Scheduler> getMesosScheduler() {
        if (allButStateStoreUninstalled(stateStore, schedulerFlags)) {
            LOGGER.info("Not registering framework because it is uninstalling.");
            return Optional.empty();
        }

        return super.getMesosScheduler();
    }

    @Override
    public Collection<Object> getResources() {
        return resources;
    }

    @Override
    protected PlanCoordinator initialize(SchedulerDriver driver) throws InterruptedException {
        LOGGER.info("Initializing...");

        // NOTE: We wait until this point to perform any work using configStore/stateStore.
        // We specifically avoid writing any data to ZK before registered() has been called.

        // Now that our SchedulerDriver has been passed in by Mesos, we can give it to the DeregisterStep in the Plan.
        uninstallPlanBuilder.registered(driver);
        offerAccepter = new OfferAccepter(Collections.singletonList(
                new UninstallRecorder(stateStore, uninstallPlanBuilder.getResourceSteps())));

        LOGGER.info("Proceeding with uninstall plan...");
        uninstallPlanManager.getPlan().proceed();

        LOGGER.info("Done initializing.");

        // Return a stub coordinator which only does work against the sole plan manager.
        return new PlanCoordinator() {
            @Override
            public List<Step> getCandidates() {
                return new ArrayList<>(uninstallPlanManager.getCandidates(Collections.emptyList()));
            }

            @Override
            public Collection<PlanManager> getPlanManagers() {
                return Collections.singletonList(uninstallPlanManager);
            }
        };
    }

    @Override
    protected void processOffers(SchedulerDriver driver, List<Protos.Offer> offers, Collection<Step> steps) {
        List<Protos.Offer> localOffers = new ArrayList<>(offers);
        // Get candidate steps to be scheduled
        if (!steps.isEmpty()) {
            LOGGER.info("Attempting to process {} candidates from uninstall plan: {}",
                    steps.size(), steps.stream().map(Element::getName).collect(Collectors.toList()));
            steps.forEach(Step::start);
        }

        // Destroy/Unreserve any reserved resource or volume that is offered
        final List<Protos.OfferID> offersWithReservedResources = new ArrayList<>();

        ResourceCleanerScheduler rcs = new ResourceCleanerScheduler(new UninstallResourceCleaner(), offerAccepter);

        offersWithReservedResources.addAll(rcs.resourceOffers(driver, localOffers));

        // Decline remaining offers.
        List<Protos.Offer> unusedOffers = OfferUtils.filterOutAcceptedOffers(localOffers, offersWithReservedResources);
        if (unusedOffers.isEmpty()) {
            LOGGER.info("No offers to be declined.");
        } else {
            LOGGER.info("Declining {} unused offers", unusedOffers.size());
            OfferUtils.declineOffers(driver, unusedOffers, Constants.LONG_DECLINE_SECONDS);
        }
    }

    @Override
    protected void processStatusUpdate(Protos.TaskStatus status) {
        stateStore.storeStatus(StateStoreUtils.getTaskName(stateStore, status), status);
    }

    private static boolean allButStateStoreUninstalled(StateStore stateStore, SchedulerFlags schedulerFlags) {
        // Because we cannot delete the root ZK node (ACLs on the master, see StateStore.clearAllData() for more
        // details) we have to clear everything under it. This results in a race condition, where DefaultService can
        // have register() called after the StateStore already has the uninstall bit wiped.
        //
        // As can be seen in DefaultService.initService(), DefaultService.register() will only be called in uninstall
        // mode if schedulerFlags.isUninstallEnabled() == true. Therefore we can use it as an OR along with
        // StateStoreUtils.isUninstalling().

        // resources are destroyed and unreserved, framework ID is gone, but tasks still need to be cleared
        return !stateStore.fetchFrameworkId().isPresent() &&
                tasksNeedClearing(stateStore);
    }

    private static boolean tasksNeedClearing(StateStore stateStore) {
        return ResourceUtils.getResourceIds(
                ResourceUtils.getAllResources(stateStore.fetchTasks())).stream()
                .allMatch(resourceId -> resourceId.startsWith(Constants.TOMBSTONE_MARKER));
    }

    @VisibleForTesting
    Plan getPlan() {
        return uninstallPlanManager.getPlan();
    }
}
