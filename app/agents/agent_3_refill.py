import logging
from app.modules.refill_prediction import RefillPredictionService

logger = logging.getLogger(__name__)


class ProactiveRefillAgent:
    """
    Agent 3: Proactive Refill Alert.
    A standalone agent that scans the Consumer Order History dataset
    and predicts if a patient is running low on their regular medication.
    """

    def __init__(self):
        self.predictor = RefillPredictionService()

    def scan_patients_for_refills(self):
        """
        Runs continuously or on a cron schedule to proactively find
        users who need refill notifications.
        """
        print("🔍 Agent 3 (Proactive Refill) scanning order history...")

        # We reuse the predictive intelligence module already written
        results = []
        try:
            # Batch predict all patients
            summary = self.predictor.batch_predict_all_patients()

            # Retrieve alerts from database
            db = self.predictor.db
            alerts = db["alerts"].find(
                {"alert_type": "refill_due", "is_resolved": False}
            )

            # Formulate outbound conversations
            for alert in alerts:
                patient_name = alert.get("patient_name") or alert.get("patient_id")
                med = alert.get("medicine_name")
                msg = f"Alert: {patient_name} is due for {med} refill. Initiating proactive chat..."
                print(f"⏰ {msg}")
                results.append(msg)

            return results
        except Exception as e:
            logger.error(f"Refill Agent failed: {e}")
            return []
