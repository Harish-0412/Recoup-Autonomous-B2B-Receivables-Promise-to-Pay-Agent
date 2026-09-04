describe("Broken-Promise Risk Scorer E2E", () => {
  beforeEach(() => {
    // Intercept API calls to FastAPI backend
    cy.intercept("POST", "**/api/score/broken_promise*", (req) => {
      req.reply({
        statusCode: 200,
        body: {
          risk_score: 0.142,
          risk_tier: "LOW",
          recommendation: "High confidence commitment. Hold escalation; wait for customer payment.",
          model_version: "v1.0.0",
          features_used: {
            customer_broken_promise_rate: 0.15,
            promise_horizon_days: 5,
          },
        },
      });
    }).as("scorePromise");

    cy.intercept("GET", "**/api/score/broken_promise/card*", {
      statusCode: 200,
      body: {
        model_name: "broken_promise_risk_scorer",
        model_version: "v1.0.0",
        algorithm: "Gradient Boosted Trees (LightGBM)",
        metrics: {
          roc_auc: 0.8993,
          accuracy: 0.819,
          f1_score: 0.7975,
        },
      },
    }).as("modelCard");

    cy.intercept("GET", "**/api/v1/invoices/INV-TEST-001*", {
      statusCode: 200,
      body: {
        invoice_id: "INV-TEST-001",
        customer_id: "CUST-001",
        customer_name: "Apex Logistics Pvt Ltd",
        amount: 85000,
        amount_paid: 15000,
        outstanding: 70000,
        currency: "INR",
        issue_date: "2026-08-01",
        due_date: "2026-08-31",
        days_overdue: 4,
        escalation_state: "monitoring",
        prior_reminders_sent: 1,
        promises: [
          {
            promise_id: "PRM-998877",
            promised_amount: 70000,
            promised_date: "2026-09-10",
            currency: "INR",
            status: "PENDING",
            created_at: "2026-09-02T10:00:00Z",
            resolved_at: null,
            broken_promise_score: 0.142,
          },
        ],
      },
    }).as("getInvoice");
  });

  it("renders the Broken-Promise Risk Scorer widget on the dashboard", () => {
    cy.visit("/dashboard");

    // Widget title and accuracy badges
    cy.contains("Broken-Promise Risk Scorer").should("be.visible");
    cy.contains("89.9% ROC-AUC").should("be.visible");
    cy.contains("Interactive Risk Simulator").should("be.visible");

    // Simulator controls
    cy.contains("Customer Broken Rate").should("be.visible");
    cy.contains("90-Day On-Time Ratio").should("be.visible");
    cy.contains("Promise Horizon").should("be.visible");
    cy.contains("Days Overdue").should("be.visible");

    // Model evaluation output
    cy.contains("Model Evaluation").should("be.visible");
    cy.contains("Agent Action Recommendation:").should("be.visible");
    cy.contains("High confidence commitment").should("be.visible");
  });

  it("displays the broken promise risk badge on the invoice promise history", () => {
    cy.visit("/invoices/INV-TEST-001");

    cy.contains("Promise history").should("be.visible");
    cy.contains("PRM-998877").should("be.visible");
    cy.contains("Broken Risk: 14% (Low Risk)").should("be.visible");
  });
});
