import logging
import numpy as np
import pandas as pd
import logging
import os
import sys

warnlog = logging.getLogger(f'warning.{__name__}')
ingredlog = logging.getLogger(f'ilog.{__name__}')

class CompoundIngredient():
    """ Calculates, stores, manages ingredients of a dataset

    Ingests the parsed data from specfied datasets and combines 
    that information with chemical inventory information.  Kicks
    off concentration calculations when appropriate data is available
    in the chemical inventories.

    Parameters
    ----------
    ingredient_series :
      ex. as generated by expworkup.ingredients.get_ingredients_df()
      _raw_reagent_2_chemicals_0_inchikey           VAWHFUNJDMQUSB-UHFFFAOYSA-N
      _raw_reagent_2_chemicals_1_inchikey           ZMXDDKWLCZADIW-UHFFFAOYSA-N
      _raw_reagent_2_instructions_2_volume                                  5.4
      _raw_reagent_2_instructions_2_volume_units                     milliliter
      _raw_reagent_2_chemicals_0_actual_amount                             2.16
      _raw_reagent_2_chemicals_0_actual_amount_units                       gram
      _raw_reagent_2_chemicals_1_actual_amount                              3.5
      _raw_reagent_2_chemicals_1_actual_amount_units                 milliliter
      name                              2020-01-23T18_13_57.034292+00_00_LBL_C9

      This can also include more or less chemicals.  MUST include
      an amount (nominal or actual), units for each amount, and inchikey
      identifiers.  Identifiers must be included in the associated 
      chem_df of the run (i.e., LBL inventory in the example above)
      The series labels should be correctly associated by chemical label 
      where "chemical_0" through "chemical_{n}" where n is the total 
      number of chemicals

    ingredient_uid_name : e.g., '2020-01-23T..._LBL_C9_reagent_2'
    
    chemdf_dict : dict of pandas.DataFrames assembled target lab 
        inventory reads in all of the chemical inventories which 
        describe the chemical content from each lab used across the 
        dataset construction.  

        The content of the inventory must meet the minimum requirements described in:
        https://github.com/darkreactions/ESCALATE_Capture/blob/master/capture/DataStructures_README.md

    return 
    --------
    self : Ingredient class object

    Notes
    ----------
    Version information for each function should be kept up to date
    ingredient = reagent = precursor

    
    TODO: version control on concentration functions
    TODO: more accessibility to other reagent information and amounts (percent composition?)
    TODO: consistency checks and validation of reagent_object concentration calculations
    """

    def __init__(self,
                 ingredient_series,
                 ingredient_uid_name,
                 chem_inventory_df):
        """
        initializes ReagentObjects

        TODO: generate class prior to call of concCalc
        TODO: build reagent objects at initial import from JSON structure
        """
        self.series = ingredient_series
        self.uid_name = ingredient_uid_name
        self.chem_df = chem_inventory_df
        self.comp_ingredient_df = self.generate_ingredient_df(self.series, self.chem_df)
        
        self.solud_model_volume = self.get_solud_model_volume(self.comp_ingredient_df)
        self.total_volume = self.solud_model_volume
        self.solv_model_volume = self.get_solv_model_volume(self.comp_ingredient_df, self.uid_name)
        #TODO: parse the insructions volume for the observed volume , calculate concentrations
#        self.observed_volume = 

        # All of these are "Chemical list" functions, i.e. they return
        #  a chemical list 
        self.inchilist = self.comp_ingredient_df['InChiKey'].tolist()
        self.smileslist = self.comp_ingredient_df['smiles'].tolist()
        self.solud_conc = self.calculate_concentration(self.comp_ingredient_df, self.solud_model_volume, 'conc_solud_v1')
        self.default_conc = self.solud_conc
        self.solv_conc = self.calculate_concentration(self.comp_ingredient_df, self.solv_model_volume, 'conc_solv_v0')
    
    def generate_ingredient_df(self,
                               ingredient_series,
                               chem_df):
        """
        Returns
        --------
        ingredient_df : dataframe as generated by generate_ingredient_df 
                                             InChiKey   amount        unit molecularmass   density       type
            chemical_num                                                                                                       
            chemicals_0   YEJRWHAVMIAJKC-UHFFFAOYSA-N       18  milliliter         86.09  1.120000    solvent
            chemicals_1   CALQKRVFTWDYDG-UHFFFAOYSA-N   5.4284        gram        201.05  1.686302    organic
            chemicals_2   RQQRAHKHDFPBMC-UHFFFAOYSA-L  12.4473        gram        461.01  6.160000  inorganic
        """
        onlychemicals_series = ingredient_series.filter(regex='_chemicals_')
        #TODO: Validate to make sure that the order of the chemicals is the same before merging
        #This should be fine though!
        onlychemicals_series.sort_index(inplace=True)

        inchikey_series = onlychemicals_series.filter(regex='inchikey')

        chemical_list_length = len(inchikey_series.values)
        chemical_id_list = [f'chemicals_{i}' for i in range(chemical_list_length)]

        try:
            chemical_series = onlychemicals_series[onlychemicals_series.index.str.endswith('_amount')]
            chemical_units = onlychemicals_series[onlychemicals_series.index.str.endswith('_units')]

            ingredient_df = pd.DataFrame({'InChiKey': inchikey_series.values,
                                          'amount': chemical_series.values, 
                                          'unit' : chemical_units.values,
                                          'chemical_num' : chemical_id_list 
                                         })
        except ValueError:
            warnlog.error(f'Please validate {self.uid_name}!  An error processing this reagent prohibits the run from completing')
            import sys
            sys.exit()

        ingredient_df['molecularmass'] = chem_df.loc[ingredient_df['InChiKey'], 'Molecular Weight (g/mol)'].values
        ingredient_df['density'] = chem_df.loc[ingredient_df['InChiKey'], 'Density            (g/mL)'].values
        ingredient_df['type'] = chem_df.loc[ingredient_df['InChiKey'], 'Chemical Category'].values
        ingredient_df['name'] = chem_df.loc[ingredient_df['InChiKey'], 'Chemical Name'].values
        ingredient_df['smiles'] = chem_df.loc[ingredient_df['InChiKey'], 'Canonical SMILES String'].values
        ingredient_df.set_index('chemical_num', inplace=True)
        ingredient_df.sort_index(inplace=True)
        return(ingredient_df)

    def get_solud_model_volume(self, comp_ingredient_df):
        """calculate the total volume 

        Calculate the total volume of the reagent solution using 
        he volume of the chemicals as an approximation

        Parameters
        ---------
        ingredient_df : dataframe as generated by generate_ingredient_df()
            see generate_ingredient_df() return for example 

        Returns
        ---------
        final_reagent_volume : estimated final solution volume
        """
        volume_total = []
        for row in comp_ingredient_df.itertuples():
            if row.unit == 'gram':
                #comp_ingredient_df.at[row.Index, 'solud_chemical_volume'] 
                volume_total.append(float(row.amount) / float(row.density))  # converts grams to mL
            elif row.unit == 'milliliter':
                #comp_ingredient_df.at[row.Index, 'solud_chemical_volume'] = row.amount  # leaves mL well enough alone
                volume_total.append(float(row.amount))
        # calculate the concentrations of each chemical using the approximated volume from above
        #final_reagent_volume = comp_ingredient_df['solud_chemical_volume'].astype(float).sum()
        return(sum(volume_total))

    def get_solv_model_volume(self, comp_ingredient_df, ingredient_uid_name):
        """calculate the total volume 

        Reagent volume approximated using the 'solvent' type chemicals as an approximation

        Parameters
        ---------
        ingredient_df : dataframe as generated by generate_ingredient_df()
            see generate_ingredient_df() return for example 

        Returns
        ---------
        final_reagent_volume : estimated final solution volume
        """

        chemical_volume = []
        for row in comp_ingredient_df.itertuples():
            if 'solvent' in row.type:
                if row.unit == 'gram':
                    warnlog.error(f'experiment with {ingredient_uid_name} specifies solvent in grams, verify this is the desired unit!')
                    chemical_volume.append(float(row.amount) / float(row.density))  # converts grams to mL
                #    comp_ingredient_df.at[row.Index, 'solv_chemical_volume'] = chemical_volume
                elif row.unit == 'milliliter':
                #    comp_ingredient_df.at[row.Index, 'solv_chemical_volume'] = row.amount  # leaves mL well enough alone
                    chemical_volume.append(float(row.amount))  # leaves mL well enough alone
            else:
                    comp_ingredient_df.at[row.Index, 'solv_chemical_volume'] = 0 
                    chemical_volume.append(0) 

        # if no volume of a defined 'solvent' is identified, default to any other liquids
        if sum(chemical_volume) == 0:
            ingredlog.info(f'experiment with {ingredient_uid_name} has no specified solvent, using sum of liquids for SolV concentration model!')
            for row in comp_ingredient_df.itertuples():
                if row.unit == 'milliliter':
                #    comp_ingredient_df.at[row.Index, 'solv_chemical_volume'] = row.amount  # leaves mL well enough alone
                    chemical_volume.append(float(row.amount))  # leaves mL well enough alone

        # if for some reason the poor chemical schmucks haven't used any liquid, try to use solids, but wtf?
        if sum(chemical_volume) == 0:
            for row in comp_ingredient_df.itertuples():
                if row.unit == 'gram':
                    warnlog.error(f'experiment with {ingredient_uid_name} has no liquids! using the solids volume.. FIX THIS!')
                    chemical_volume.append(float(row.amount) / float(row.density))  # converts grams to mL

        return(sum(chemical_volume))


        #final_reagent_volume = comp_ingredient_df['solv_chemical_volume'].astype(float).sum()
        
    def calculate_concentration(self,
                                comp_ingredient_df, 
                                final_reagent_volume,
                                calc_name):
        """calculate concentration of each chemical in reagent based on volume provided
            
        v1 input is dataframe of the reagent information as parsed from JSON from google drive
        based on assumptions and calculations outlined in density paper DOI: 10.18126/LYK3-QACE 
        This code requires density for all values in the dataset!

        Parameters
        ----------
        ingredient_df : dataframe as generated by generate_ingredient_df()
            see generate_ingredient_df() return for example 
        
        Returns
        --------
        conc_df : concentration of each chemical based on the approximated total volume
        e.g.
                       chemicals_0  chemicals_1  chemicals_2
        conc_solud_v1    10.076409     1.161812     1.161804
                         
                       chemicals_0  chemicals_1  chemicals_2
        conc_solv_v0     10.076409     1.161812     1.161804

        Notes
        --------
            Key Scientific Assumptions
                * Non-ideality (change in volume due to mixing) is ignored
                * Density values are calculated from molecular volumes and 
                    emperically corrected (see doi above)
        """
        #conc_df = pd.DataFrame()
        conc_list = []
        for row in comp_ingredient_df.itertuples():
            if row.unit == 'gram':
                calculated_concentration = float(row.amount) / float(row.molecularmass) / \
                                                 float(final_reagent_volume / 1000)  # g --> mol --> [M] (v1-conc)
                # conc_df.loc[row.Index, calc_name] = calculated_concentration
                conc_list.append(calculated_concentration)
            elif row.unit == 'milliliter':
                calculated_concentration = float(row.amount) * float(row.density) / \
                                            float(row.molecularmass) / float(final_reagent_volume / 1000)
                # conc_df.loc[row.Index, calc_name] = calculated_concentration
                conc_list.append(calculated_concentration)
        return(conc_list)
